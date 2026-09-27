// SPDX-License-Identifier: GPL-3.0-or-later
// Admission occurs AFTER authentication and BEFORE DNS/outbound socket creation.
package route

import (
	"context"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"

	"github.com/sagernet/sing-box/option"
	R "github.com/sagernet/sing-box/route/rule"
	N "github.com/sagernet/sing/common/network"
)

type userLimitState struct {
	tcp, udp    int
	tokens      float64
	last        time.Time
	lastWarning time.Time
	rejected    uint64
}

type userLimiter struct {
	mu         sync.Mutex
	options    *option.UserLimitsOptions
	users      map[string]*userLimitState
	operations uint64
}

func newUserLimiter(options *option.UserLimitsOptions) *userLimiter {
	return &userLimiter{options: options, users: make(map[string]*userLimitState)}
}

func (l *userLimiter) validate() error {
	if l.options == nil {
		return nil
	}
	if l.options.Scope != "user" && l.options.Scope != "node_user" {
		return fmt.Errorf("user_limits.scope must be user or node_user")
	}
	check := func(v option.UserLimitValues) error {
		if v.TCP < 0 || v.UDP < 0 || v.Total < 0 || v.Rate < 0 || v.Burst < 0 {
			return fmt.Errorf("user_limits values must be non-negative")
		}
		if (v.Rate == 0) != (v.Burst == 0) {
			return fmt.Errorf("user_limits rate and burst must both be zero or positive")
		}
		return nil
	}
	if err := check(l.options.Defaults); err != nil {
		return err
	}
	for key, v := range l.options.Users {
		if key == "" {
			return fmt.Errorf("user_limits override key is empty")
		}
		if err := check(v); err != nil {
			return err
		}
	}
	return nil
}

// Returns an idempotent release callback, acceptance, and an optional throttled log.
func (l *userLimiter) acquire(user string, udp bool, now time.Time) (func(), bool, string) {
	if l == nil || l.options == nil || user == "" {
		return func() {}, true, ""
	}
	key := user
	if l.options.Scope == "user" {
		if at := strings.LastIndex(key, ":"); at >= 0 {
			key = key[at+1:]
		}
	}
	limit := l.options.Defaults
	if override, ok := l.options.Users[key]; ok {
		limit = override
	}
	l.mu.Lock()
	l.operations++
	if l.operations%256 == 0 {
		for name, state := range l.users {
			if state.tcp+state.udp == 0 && now.Sub(state.last) > 5*time.Minute {
				delete(l.users, name)
			}
		}
	}
	state := l.users[key]
	if state == nil {
		state = &userLimitState{tokens: float64(limit.Burst), last: now}
		l.users[key] = state
	}
	if limit.Rate > 0 {
		elapsed := now.Sub(state.last).Seconds()
		if elapsed > 0 {
			state.tokens += elapsed * float64(limit.Rate)
		}
		if state.tokens > float64(limit.Burst) {
			state.tokens = float64(limit.Burst)
		}
	}
	state.last = now
	// Count attempts against the bucket too, so rejected floods cannot dodge the rate limit.
	rateOK := limit.Rate == 0 || state.tokens >= 1
	if limit.Rate > 0 && state.tokens >= 1 {
		state.tokens--
	}
	allowed := rateOK && (limit.Total == 0 || state.tcp+state.udp < limit.Total) &&
		((!udp && (limit.TCP == 0 || state.tcp < limit.TCP)) || (udp && (limit.UDP == 0 || state.udp < limit.UDP)))
	if !allowed {
		state.rejected++
		message := ""
		if state.lastWarning.IsZero() || now.Sub(state.lastWarning) >= 30*time.Second {
			message = fmt.Sprintf("user-limit user=%s tcp=%d udp=%d rejected=%d", key, state.tcp, state.udp, state.rejected)
			state.lastWarning = now
		}
		l.mu.Unlock()
		return nil, false, message
	}
	if udp {
		state.udp++
	} else {
		state.tcp++
	}
	l.mu.Unlock()
	var once sync.Once
	release := func() {
		once.Do(func() {
			l.mu.Lock()
			if udp {
				state.udp--
			} else {
				state.tcp--
			}
			l.mu.Unlock()
		})
	}
	return release, true, ""
}

func (r *Router) admitUser(ctx context.Context, user string, udp bool) (func(), error) {
	release, allowed, message := r.userLimits.acquire(user, udp, time.Now())
	if message != "" {
		r.logger.WarnContext(ctx, message)
	}
	if !allowed {
		return nil, &R.RejectedError{Cause: fmt.Errorf("user session limit reached")}
	}
	return release, nil
}

type limitedConn struct {
	net.Conn
	release func()
}

func (c *limitedConn) Close() error  { defer c.release(); return c.Conn.Close() }
func (c *limitedConn) Upstream() any { return c.Conn }

// Do not advertise replaceable readers/writers: that would bypass this Close wrapper.
type limitedPacketConn struct {
	N.PacketConn
	release func()
}

func (c *limitedPacketConn) Close() error { defer c.release(); return c.PacketConn.Close() }

// Preserve headroom and handshake discovery without permitting Close to be bypassed.
func (c *limitedPacketConn) Upstream() any { return c.PacketConn }

func limitOnClose(release func(), next N.CloseHandlerFunc) N.CloseHandlerFunc {
	return N.OnceClose(func(err error) {
		release()
		if next != nil {
			next(err)
		}
	})
}
