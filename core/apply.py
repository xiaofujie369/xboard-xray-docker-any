"""Apply the small auditable limiter overlay to the exact upstream source layout."""
from pathlib import Path
import shutil
import sys


def replace(path, old, new, count=1):
    text = path.read_text(encoding='utf-8')
    if text.count(old) != count:
        raise RuntimeError(f'Upstream source mismatch: {path}; do not build an unverified core')
    path.write_text(text.replace(old, new), encoding='utf-8', newline='\n')


def main():
    target = Path(sys.argv[1])
    here = Path(__file__).resolve().parent
    replace(target / 'option/route.go', 'type RouteOptions struct {',
            'type RouteOptions struct {\n\tUserLimits *UserLimitsOptions `json:"user_limits,omitempty"`')
    replace(target / 'route/router.go', 'type Router struct {',
            'type Router struct {\n\tuserLimits *userLimiter')
    replace(target / 'route/router.go', 'return &Router{',
            'return &Router{\n\t\tuserLimits: newUserLimiter(options.UserLimits),')
    replace(target / 'route/router.go', 'func (r *Router) Initialize(rules []option.Rule, ruleSets []option.RuleSet) error {',
            'func (r *Router) Initialize(rules []option.Rule, ruleSets []option.RuleSet) error {\n'
            '\tif err := r.userLimits.validate(); err != nil { return err }')
    # Legacy UDP callers (including multi-user SS) must not wait forever after rejection.
    replace(target / 'route/route.go',
            '\t\t\tr.logger.ErrorContext(ctx, err)\n\t\t}\n\t}\n\tselect {',
            '\t\t\tr.logger.ErrorContext(ctx, err)\n\t\t}\n\t\treturn err\n\t}\n\tselect {')
    for name, kind, packet in [('routeConnection', 'net.Conn', False), ('routePacketConnection', 'N.PacketConn', True)]:
        signature = f'func (r *Router) {name}(ctx context.Context, conn {kind}, metadata adapter.InboundContext, onClose N.CloseHandlerFunc) error {{'
        replace(target / 'route/route.go', signature, signature.replace(') error {', ') (retErr error) {'))
    text = (target / 'route/route.go').read_text()
    for name, wrapper, udp in [('routeConnection', 'limitedConn', 'false'), ('routePacketConnection', 'limitedPacketConn', 'true')]:
        start = text.index(f'func (r *Router) {name}(')
        location = text.index('\tconntrack.KillerCheck()', start)
        hook = ('\trelease, limitErr := r.admitUser(ctx, metadata.User, ' + udp + ')\n'
                '\tif limitErr != nil { return limitErr }\n'
                '\tconn = &' + wrapper + '{conn, release}\n'
                '\tonClose = limitOnClose(release, onClose)\n'
                '\tdefer func() { if retErr != nil { release() } }()\n')
        text = text[:location] + hook + text[location:]
    (target / 'route/route.go').write_text(text, encoding='utf-8', newline='\n')
    for source, dest in [('user_limits_options.go', 'option/user_limits.go'),
                         ('user_limits.go', 'route/user_limits.go'),
                         ('user_limits_test.go', 'route/user_limits_test.go')]:
        shutil.copyfile(here / source, target / dest)


if __name__ == '__main__':
    main()
