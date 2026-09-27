// SPDX-License-Identifier: GPL-3.0-or-later
package route

import (
	"context"
	"github.com/sagernet/sing-box/adapter"
	"github.com/sagernet/sing-box/log"
	"github.com/sagernet/sing-box/option"
	N "github.com/sagernet/sing/common/network"
	"net"
	"sync"
	"testing"
	"time"
)

type rejectedPacket struct {
	N.PacketConn
	closed bool
}

func (p *rejectedPacket) Close() error { p.closed = true; return nil }

func TestUserLimitsLegacyUDPRejectReturns(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	l := newUserLimiter(&option.UserLimitsOptions{Scope: "user", Defaults: option.UserLimitValues{UDP: 1}})
	release, _, _ := l.acquire("1:7", true, time.Now())
	defer release()
	r := &Router{ctx: ctx, userLimits: l, logger: log.NewNOPFactory().NewLogger("test")}
	conn := &rejectedPacket{}
	done := make(chan error, 1)
	go func() { done <- r.RoutePacketConnection(ctx, conn, adapter.InboundContext{User: "1:7"}) }()
	select {
	case err := <-done:
		if err == nil || !conn.closed {
			t.Fatal("rejection did not close and return")
		}
	case <-time.After(time.Second):
		t.Fatal("rejected legacy UDP leaked a waiting goroutine")
	}
}

func TestUserLimitsCapsAndIdentity(t *testing.T) {
	l := newUserLimiter(&option.UserLimitsOptions{Scope: "user", Defaults: option.UserLimitValues{TCP: 2, UDP: 1, Total: 2}})
	now := time.Now()
	a, ok, _ := l.acquire("408:7", false, now)
	if !ok {
		t.Fatal("first TCP")
	}
	b, ok, _ := l.acquire("379:7", true, now)
	if !ok {
		t.Fatal("first UDP")
	}
	if _, ok, _ := l.acquire("408:7", false, now); ok {
		t.Fatal("cross-node total bypass")
	}
	c, ok, _ := l.acquire("408:8", false, now)
	if !ok {
		t.Fatal("other user blocked")
	}
	c()
	a()
	a()
	if _, ok, _ := l.acquire("408:7", true, now); ok {
		t.Fatal("UDP cap bypass")
	}
	c, ok, _ = l.acquire("408:7", false, now)
	if !ok {
		t.Fatal("slot not released")
	}
	b()
	c()
	if l.users["7"].tcp != 0 || l.users["7"].udp != 0 {
		t.Fatal("counter leak")
	}
	l.options.Scope = "node_user"
	a, _, _ = l.acquire("408:7", true, now)
	b, ok, _ = l.acquire("379:7", true, now)
	if !ok {
		t.Fatal("node scope")
	}
	a()
	b()
}

func TestUserLimitsRateAndOverride(t *testing.T) {
	l := newUserLimiter(&option.UserLimitsOptions{Scope: "user", Defaults: option.UserLimitValues{Rate: 2, Burst: 2}, Users: map[string]option.UserLimitValues{"8": {TCP: 1}}})
	now := time.Now()
	for i := 0; i < 2; i++ {
		release, ok, _ := l.acquire("1:7", false, now)
		if !ok {
			t.Fatal("burst")
		}
		release()
	}
	if _, ok, _ := l.acquire("1:7", false, now); ok {
		t.Fatal("rate bypass")
	}
	release, ok, _ := l.acquire("1:7", false, now.Add(500*time.Millisecond))
	if !ok {
		t.Fatal("refill")
	}
	release()
	release, ok, _ = l.acquire("1:8", false, now)
	if !ok {
		t.Fatal("override")
	}
	if _, ok, _ := l.acquire("2:8", false, now); ok {
		t.Fatal("override cap")
	}
	release()
}

func TestUserLimitsConcurrentAndClose(t *testing.T) {
	l := newUserLimiter(&option.UserLimitsOptions{Scope: "user", Defaults: option.UserLimitValues{Total: 4}})
	var workers sync.WaitGroup
	for i := 0; i < 100; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for j := 0; j < 100; j++ {
				release, ok, _ := l.acquire("1:7", false, time.Now())
				if ok {
					release()
					release()
				}
			}
		}()
	}
	workers.Wait()
	if l.users["7"].tcp != 0 {
		t.Fatal("concurrent counter leak")
	}
	release, _, _ := l.acquire("1:7", false, time.Now())
	a, b := net.Pipe()
	defer b.Close()
	wrapped := &limitedConn{a, release}
	_ = wrapped.Close()
	calls := 0
	closed := limitOnClose(release, func(error) { calls++ })
	closed(nil)
	closed(nil)
	if calls != 1 || l.users["7"].tcp != 0 {
		t.Fatal("double-close accounting")
	}
}

func TestUserLimitsValidation(t *testing.T) {
	for _, o := range []option.UserLimitsOptions{{Scope: "bad"}, {Scope: "user", Defaults: option.UserLimitValues{TCP: -1}}, {Scope: "user", Defaults: option.UserLimitValues{Rate: 1}}} {
		if newUserLimiter(&o).validate() == nil {
			t.Fatal("invalid config accepted")
		}
	}
	if newUserLimiter(nil).validate() != nil {
		t.Fatal("disabled")
	}
}
