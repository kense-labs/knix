#!/bin/bash
# Guest-owned health snapshot verification. Runs inside the guest as root.
# Pure shell: no jq, no python3, no host input, no writes to /var/lib/knix.
set -eu
fail() { echo "STATUS VERIFY FAILED: $*" >&2; exit 1; }

CMD=/usr/local/bin/knix-status
SCRIPT=/usr/lib/knix/knix-status.sh
PDIR=/var/lib/knix
DRILL=/run/knix/status-drill
OUT=/run/knix/status-verify.txt
JOUT=/run/knix/status-verify.json

install -d -m 0755 /run/knix
[ -x "$CMD" ] || fail "missing /usr/local/bin/knix-status"
[ -x "$SCRIPT" ] || fail "missing guest-owned status script"
[ -L "$CMD" ] || fail "health snapshot entry point is not a guest-owned link"
[ "$(readlink "$CMD")" = "$SCRIPT" ] \
  || fail "health snapshot entry point points outside the guest script"

"$CMD" > "$OUT" || fail "a healthy guest reported a degraded status"
"$CMD" --json > "$JOUT" || fail "the --json snapshot failed"

val() { sed -n "s/^$1=\(.*\)$/\1/p" "$OUT" | head -1; }

[ "$(val owner)" = guest ] || fail "snapshot is not guest-owned"
[ "$(val status)" = ok ] || fail "guest status is not ok ($(val status))"
[ "$(val mode)" = graphics ] || fail "guest is not running the graphics session"
[ "$(val product)" = knix ] || fail "guest identity drifted"
[ "$(val boot)" = uefi-self ] || fail "guest did not self-boot from its own ESP"
[ "$(val session)" = active ] || fail "guest session is not active"
[ "$(val session_enabled)" = enabled ] || fail "guest session is not enabled"
[ "$(val compositor_user)" = knix ] || fail "compositor is not knix-owned"
[ "$(val seat)" = active ] || fail "guest seat manager is not active"
[ "$(val drm)" = present ] || fail "guest DRM device is missing"
[ "$(val permitrootlogin)" = no ] || fail "sshd accepts root login"
[ "$(val passwordauthentication)" = no ] || fail "sshd accepts passwords"
[ "$(val pubkeyauthentication)" = yes ] || fail "sshd refuses public keys"

[ "$(val consistency_checks)" = 5 ] || fail "snapshot lost a consistency check"
[ "$(val consistency_ok)" = 5 ] || fail "not all consistency checks passed"
for c in archive_vs_stats stats_vs_trends boots_vs_boots rows_vs_sum sources_present; do
  [ "$(val "consistency_$c")" = ok ] || fail "consistency check failed: $c"
done
AE="$(val archive_events)"; ST="$(val stats_total_events)"
SB="$(val stats_boots_seen)"; TT="$(val trends_total_events)"
TB="$(val trends_boots_seen)"; TS="$(val trend_events_sum)"
RS="$(val trend_row_sum)"; RN="$(val trend_rows)"
for n in "$AE" "$ST" "$SB" "$TT" "$TB" "$TS" "$RS" "$RN"; do
  case "$n" in ''|*[!0-9]*) fail "non-numeric health field: $n" ;; esac
done
[ "$AE" -ge 2 ] || fail "guest archive has too few events"
[ "$AE" = "$ST" ] || fail "archive events != guest statistics"
[ "$ST" = "$TT" ] || fail "guest statistics != guest trends"
[ "$SB" = "$TB" ] || fail "statistics boots != trend boots"
[ "$RS" = "$TS" ] || fail "derived trend row sum != written trend sum"
[ "$RN" -ge 1 ] || fail "guest trend report has no rows"
[ "$(val alert)" = present ] || fail "guest persistent alert is missing"

grep -q '"owner": "guest"' "$JOUT" || fail "json snapshot lost guest ownership"
grep -q '"status": "ok"' "$JOUT" || fail "json snapshot lost its verdict"
grep -q '"consistency": {' "$JOUT" || fail "json snapshot lost consistency"
grep -q '"diagnostics": {' "$JOUT" || fail "json snapshot lost diagnostics"

# The self-check must be falsifiable, not just a happy-path printer. Inject two
# inconsistencies into an isolated /run copy and require the command to refuse
# them. Production evidence on the root disk must stay byte-identical.
rm -rf "$DRILL"
install -d -m 0755 "$DRILL/var"
for f in session-failure.log session-failure.stats session-failure.trends; do
  [ -r "$PDIR/$f" ] || fail "missing production diagnostics source: $f"
  cp "$PDIR/$f" "$DRILL/var/$f"
done
BEFORE="$(sha256sum "$PDIR/session-failure.log" "$PDIR/session-failure.stats" "$PDIR/session-failure.trends")"

# Case A: statistics no longer match the archive.
sed -i "s/^total_events=.*/total_events=$((ST + 1))/" \
  "$DRILL/var/session-failure.stats"
if KNIX_STATUS_DRILL_DIR="$DRILL/var" "$CMD" > "$DRILL/case-a.txt" 2>&1; then
  fail "injected statistics drift was accepted"
fi
grep -q '^status=degraded$' "$DRILL/case-a.txt" \
  || fail "statistics drift did not produce a degraded verdict"
grep -q '^reasons=.*archive_vs_stats' "$DRILL/case-a.txt" \
  || fail "statistics drift was not attributed to archive_vs_stats"
grep -q '^reasons=.*stats_vs_trends' "$DRILL/case-a.txt" \
  || fail "statistics drift was not attributed to stats_vs_trends"

# Case B: the written row sum no longer matches the rows. The command must
# derive the sum from the rows rather than trusting the field it just read.
cp "$PDIR/session-failure.stats" "$DRILL/var/session-failure.stats"
sed -i "s/^trend_events_sum=.*/trend_events_sum=$((TS + 1))/" \
  "$DRILL/var/session-failure.trends"
if KNIX_STATUS_DRILL_DIR="$DRILL/var" "$CMD" > "$DRILL/case-b.txt" 2>&1; then
  fail "injected trend row-sum drift was accepted"
fi
grep -q '^status=degraded$' "$DRILL/case-b.txt" \
  || fail "trend row-sum drift did not produce a degraded verdict"
grep -q '^reasons=.*rows_vs_sum' "$DRILL/case-b.txt" \
  || fail "trend row-sum drift was not attributed to rows_vs_sum"

# Restoring the isolated copy must return to a healthy verdict, and the
# production evidence must be untouched by the whole drill.
for f in session-failure.log session-failure.stats session-failure.trends; do
  cp "$PDIR/$f" "$DRILL/var/$f"
done
KNIX_STATUS_DRILL_DIR="$DRILL/var" "$CMD" > "$DRILL/control.txt" \
  || fail "restored drill copy is not healthy"
grep -q '^status=ok$' "$DRILL/control.txt" \
  || fail "restored drill copy did not report ok"
AFTER="$(sha256sum "$PDIR/session-failure.log" "$PDIR/session-failure.stats" "$PDIR/session-failure.trends")"
[ "$BEFORE" = "$AFTER" ] \
  || fail "the status drill rewrote production diagnostics evidence"
grep -q '^status=ok$' "$OUT" || fail "production snapshot changed during the drill"
rm -rf "$DRILL"

echo "STATUS CONSISTENCY: 5/5 (archive $AE = stats $ST = trends $TT, boots $SB = $TB, rows sum $RS)"
echo "STATUS DRILL: 2 refusals (statistics drift, trend row-sum drift), production evidence byte-identical"
echo "STATUS VERIFY OK: guest-owned knix-status reconciles 6 sources in text and --json"
