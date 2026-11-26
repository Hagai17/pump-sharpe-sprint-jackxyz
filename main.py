#!/usr/bin/env python3
"""pump.fun SHARPE sprint bot - "steady whale-follow" (sweep 4, jackxyz-sharpe).

Design (ranking metric = hourly Sharpe, prize needs net PnL > 0):
  * Main sleeve: follow only the largest prints. A single recorded buy of
    >= 5 SOL within the last 3 tape entries, net 30-slot flow >= +0.5 SOL and
    >= 2 distinct buyers - the one flow signal with positive expectancy net of
    the ~2.5% round-trip curve fees in offline replay. Fixed 300-slot hold,
    -10% liquidation-value stop, no profit target (targets truncated the tail
    that pays for the fees in every local test).
  * Scout sleeve: minimum-size (0.011 SOL) instant round trips on the most
    liquid curves, paced at ~3.4 fresh mints/hour, guaranteeing the guardrail
    of >= 20 distinct round-tripped mints at a small, evenly spread cost.
  * Risk: stake capped by the 1%-of-reserves size cap, <= 5 open positions,
    <= 2.1 SOL committed, no entries in the last 240 s of an hour bucket,
    failed/rejected exits are retried (a 2% seeded tx-failure draw otherwise
    strands positions forever).
Deterministic: no clock/env/unseeded randomness; hour buckets derive from
event timestamps only.
"""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "vendor"))
import makefun_bot as _makefun_bot

"""Sharpe-first pump.fun replay strategy (shared by offline sim and shipped main.py).

Design: small stakes, event-driven entries on whale buys / accelerating inflow,
fast shaped exits (TP / trailing / stop / time / fade), exposure cap, hour-bucket
discipline (no fresh entries in the last minutes of an hour mark), and a scout
sleeve that paces the 20-coin round-trip guardrail with minimum-size round trips.
Deterministic: only batch data, insertion-ordered iteration, no clock/env/random.
"""
import collections

LAMPORTS = 1_000_000_000
TX_FEE = 105_000
FEE_BPS = 125  # 95 protocol + 30 creator, uniform in the replay data

P = {
    "stake": 50_000_000,          # 0.05 SOL main stake
    "scout_stake": 11_000_000,    # 0.011 SOL scout round trips (guardrail pace)
    "min_cap": 20_000_000,        # need size cap >= 0.02 SOL to enter
    "max_positions": 5,
    "max_exposure": 350_000_000,  # 0.35 SOL total cost across positions
    "rq_min": 1_500_000_000,      # 1.5 SOL real reserves
    "rq_max": 84_000_000_000,     # stay below completion
    "min_age": 100,               # slots since create
    "whale_buy": 1_500_000_000,   # single recorded buy >= 1.5 SOL
    "mom_net30": 2_000_000_000,   # net inflow over 30 slots >= 2 SOL
    "mom_up_min": 0.05,
    "mom_up_max": 0.45,
    "mom_buyers": 4,
    "chase_max": 0.60,            # skip if price up > 60% over 30 slots
    "tp": 0.10,                   # take profit at +10% of cost (close value)
    "trail_arm": 0.15,
    "trail_drop": 0.20,
    "stop": 0.06,                 # stop loss at -6% of cost
    "max_hold": 900,              # slots
    "fade_net15": -1_000_000_000, # exit on net outflow >= 1 SOL over 15 slots
    "cooldown": 300,
    "max_entries": 3,
    "buy_slip": 3000,
    "sell_slip": 10000,
    "hour_guard_secs": 240,       # no new entries in last 240s of an hour bucket
    "scout_coins_target": 20,
    "scout_per_hour": 3.4,        # pace: coins per elapsed hour
    "tape_keep": 900,             # slots of tape retained
}

# NOTE: DEFAULTS above are the early research settings, kept so the offline
# sweep harness can reproduce every iteration in candidates.md. The graded
# entry point at the bottom of this file overrides every economically
# relevant parameter explicitly - tuning DEFAULTS does not change the
# submission.


def close_value(tokens, vt, vq):
    """What closing `tokens` now pays, net of curve fees and the tx fee (engine closeQuote)."""
    if tokens <= 0 or vt <= 0:
        return 0
    q = (tokens * vq) // (vt + tokens)
    fee = -(-q * 95 // 10000) + -(-q * 30 // 10000)
    net = q - fee - TX_FEE
    return net if net > 0 else 0


class Strat:
    def __init__(self, params=None):
        self.p = dict(P)
        if params:
            self.p.update(params)
        self.tapes = {}       # mint -> deque[(slot, ts, is_buy, sol, user, price)]
        self.creators = {}    # mint -> creator wallet
        self.t0 = None
        self.now = 0
        self.held = {}        # mint -> dict(slot, peak, exiting, scout)
        self.last_exit = {}   # mint -> slot
        self.entries = collections.Counter()
        self.trip_coins = set()   # mints with a completed round trip >= 0.01 SOL (own fills)
        self.bought = collections.defaultdict(int)  # mint -> lamports bought in current trip
        self.open_mints = set()
        self.scout_mint = None
        self.bucket = -1          # current hour bucket index
        self.bucket_equity0 = 0   # equity estimate at bucket start
        self.bucket_entries = 0   # main entries opened in this bucket

    # ---------- tape ----------
    def _feed(self, batch):
        for e in batch["events"]:
            ts = e.get("timestamp")
            if ts is not None:
                t = int(ts)
                if self.t0 is None:
                    self.t0 = t
                if t > self.now:
                    self.now = t
            if e["event"] == "CreateEvent":
                if e.get("creator"):
                    self.creators[e["mint"]] = e["creator"]
                continue
            if e["event"] != "TradeEvent":
                continue
            mint = e["mint"]
            dq = self.tapes.get(mint)
            if dq is None:
                dq = self.tapes[mint] = collections.deque()
            vq = int(e["virtual_quote_reserves"]); vt = int(e["virtual_token_reserves"])
            price = vq / vt if vt else 0.0
            dq.append((int(e["slot"]), self.now, bool(e["is_buy"]), int(e["sol_amount"]), e["user"], price))
            cutoff = int(e["slot"]) - self.p["tape_keep"]
            while dq and dq[0][0] < cutoff:
                dq.popleft()

    def _window(self, mint, back, slot):
        dq = self.tapes.get(mint)
        if not dq:
            return []
        return [x for x in dq if x[0] > slot - back]

    # ---------- accounting from fills ----------
    def _account(self, state, batch):
        for f in batch["fills"]:
            mint = f["mint"]
            if f["side"] == "sell" and f["status"] in ("failed", "rejected"):
                # the exit did not happen: allow the exit logic to retry next batch
                h = self.held.get(mint)
                if h is not None:
                    h["exiting"] = False
            if f["status"] != "filled":
                continue
            if f["side"] == "buy":
                self.bought[mint] += int(f["quote_lamports"])
                self.open_mints.add(mint)
            elif f["side"] == "sell":
                if mint not in state.positions:
                    if self.bought[mint] >= 10_000_000:
                        self.trip_coins.add(mint)
                    self.bought[mint] = 0
                    self.open_mints.discard(mint)
                    if self.scout_mint == mint:
                        self.scout_mint = None
        # positions that vanished via harness closes
        for mint in list(self.open_mints):
            if mint not in state.positions and mint not in {pf["mint"] for pf in batch["fills"]}:
                if self.bought[mint] >= 10_000_000:
                    self.trip_coins.add(mint)
                self.bought[mint] = 0
                self.open_mints.discard(mint)
                if self.scout_mint == mint:
                    self.scout_mint = None

    # ---------- signals ----------
    def _entry_signal(self, state, mint, slot):
        p = self.p
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            return False
        if curve.created_slot is not None and slot - curve.created_slot < p["min_age"]:
            return False
        rq = curve.real_quote_reserves
        if not (p["rq_min"] <= rq <= p["rq_max"]):
            return False
        w30 = self._window(mint, 30, slot)
        if len(w30) < 3:
            return False
        net30 = sum(x[3] if x[2] else -x[3] for x in w30)
        up30 = w30[-1][5] / w30[0][5] - 1.0 if w30[0][5] > 0 else 0.0
        if up30 > p["chase_max"]:
            return False
        whale = any(x[2] and x[3] >= p["whale_buy"] for x in w30[-p.get("whale_lookback", 6):])
        mode = p.get("entry_mode", "both")
        if mode == "capit":
            # capitulation base: deep drawdown within 300 slots, then a flat,
            # flow-positive 60-slot base on a still-liquid curve
            if not (5_000_000_000 <= rq <= p["rq_max"]):
                return False
            w300 = self._window(mint, 300, slot)
            w60 = self._window(mint, 60, slot)
            if len(w300) < 10 or len(w60) < 6:
                return False
            peak = max(x[5] for x in w300)
            if peak <= 0 or w300[-1][5] / peak - 1.0 > -0.25:
                return False
            hi = max(x[5] for x in w60); lo = min(x[5] for x in w60)
            if lo <= 0 or hi / lo - 1.0 > 0.05:
                return False
            return sum(x[3] if x[2] else -x[3] for x in w60) > 0
        if mode == "whale2":
            whales = sum(1 for x in w30 if x[2] and x[3] >= p["whale_buy"])
            return whales >= 2 and net30 > 0
        if whale and net30 > p.get("net30_min", 0) and mode in ("both", "whale"):
            if p.get("min_buyers30", 0):
                if len({x[4] for x in w30 if x[2]}) < p["min_buyers30"]:
                    return False
            if p.get("whale_age_min", 0) and curve.created_slot is not None and slot - curve.created_slot < p["whale_age_min"]:
                return False
            if p.get("whale_not_creator"):
                creator = self.creators.get(mint)
                wx = [x for x in w30[-6:] if x[2] and x[3] >= p["whale_buy"]]
                if creator and all(x[4] == creator for x in wx):
                    return False
            return True
        buyers = len({x[4] for x in w30 if x[2]})
        if net30 >= p["mom_net30"] and p["mom_up_min"] <= up30 <= p["mom_up_max"] and buyers >= p["mom_buyers"] and mode in ("both", "mom"):
            return True
        return False

    # ---------- main ----------
    def _equity_est(self, state):
        e = state.cash
        for mint, pos in state.positions.items():
            curve = state.curves.get(mint)
            if curve is not None:
                e += close_value(pos.token_amount, curve.virtual_token_reserves, curve.virtual_quote_reserves)
        return e

    def on_batch(self, state, batch):
        p = self.p
        slot = batch["slot"]
        self._feed(batch)
        self._account(state, batch)
        orders = []

        # ---- hour governor: snapshot equity at each new hour bucket; stop opening
        # new main positions once the hour is won (+hour_lock) or lost (-hour_stop)
        self.hour_blocked = False
        if self.t0 is not None and p.get("hour_stop", 0):
            b = (self.now - self.t0) // 3600
            if b != self.bucket:
                self.bucket = b
                self.bucket_equity0 = self._equity_est(state)
                self.bucket_entries = 0
            eq = self._equity_est(state)
            if self.bucket_equity0 > 0:
                chg = eq / self.bucket_equity0 - 1.0
                if chg <= -p["hour_stop"] or chg >= p.get("hour_lock", 9.0):
                    self.hour_blocked = True
            if self.bucket_entries >= p.get("max_per_hour", 99):
                self.hour_blocked = True

        # sync held bookkeeping
        for mint in list(self.held):
            if mint not in state.positions and mint not in state.pending and \
                    not any(pp.order["mint"] == mint for pp in state.pending.values()):
                del self.held[mint]
        # safety: an exit marked but with no sell in flight and the position still
        # there means the exit never landed (e.g. a fill we could not classify)
        pending_mints = {pp.order["mint"] for pp in state.pending.values()}
        for mint, h in self.held.items():
            if h["exiting"] and mint in state.positions and mint not in pending_mints:
                h["exiting"] = False
        for mint in state.positions:
            if mint not in self.held:
                self.held[mint] = {"slot": slot, "peak": 0, "exiting": False,
                                   "scout": mint == self.scout_mint}

        # ---- exits ----
        for mint, h in list(self.held.items()):
            pos = state.positions.get(mint)
            if pos is None or h["exiting"]:
                continue
            curve = state.curves.get(mint)
            if curve is None:
                continue
            val = close_value(pos.token_amount, curve.virtual_token_reserves, curve.virtual_quote_reserves)
            cost = pos.cost_lamports
            if val > h["peak"]:
                h["peak"] = val
            sell = False
            if h["scout"]:
                sell = True  # scout: sell as soon as the buy has landed
            elif p.get("time_exit", 0) > 0:
                if slot - h["slot"] >= p["time_exit"]:
                    sell = True
                elif cost > 0 and val <= cost * (1 - p["stop"]):
                    sell = True
            elif cost > 0 and val <= cost * (1 - p["stop"]):
                sell = True
            elif cost > 0 and val >= cost * (1 + p["tp"]):
                sell = True
            elif h["peak"] >= cost * (1 + p["trail_arm"]) and val <= h["peak"] * (1 - p["trail_drop"]):
                sell = True
            elif slot - h["slot"] > p["max_hold"]:
                sell = True
            else:
                w15 = self._window(mint, 15, slot)
                if len(w15) >= 3 and sum(x[3] if x[2] else -x[3] for x in w15) <= p["fade_net15"]:
                    sell = True
            if sell:
                orders.append({"side": "sell", "mint": mint, "token_amount": pos.token_amount,
                               "max_slippage_bps": p["sell_slip"]})
                h["exiting"] = True
                self.last_exit[mint] = slot

        # ---- hour guard ----
        in_guard = False
        if self.t0 is not None:
            phase = (self.now - self.t0) % 3600
            in_guard = phase > 3600 - p["hour_guard_secs"]

        # ---- entries ----
        committed = state.committed_position_count()
        exposure = sum(pp.cost_lamports for pp in state.positions.values())
        if not in_guard and not self.hour_blocked and committed < p["max_positions"]:
            seen = []
            seen_set = set()
            for e in batch["events"]:
                if e["event"] == "TradeEvent" and e["mint"] not in seen_set:
                    seen_set.add(e["mint"])
                    seen.append(e["mint"])
            for mint in seen:
                if len(orders) >= 14 or committed >= p["max_positions"]:
                    break
                if mint in state.positions or mint in self.held:
                    continue
                if self.entries[mint] >= p["max_entries"]:
                    continue
                if slot - self.last_exit.get(mint, -10**9) < p["cooldown"]:
                    continue
                if any(pp.order["mint"] == mint for pp in state.pending.values()):
                    continue
                if not self._entry_signal(state, mint, slot):
                    continue
                cap = state.max_buy_lamports(mint)
                stake = min(p["stake"], cap)
                if stake < p["min_cap"]:
                    continue
                if exposure + stake > p["max_exposure"]:
                    continue
                if state.free_cash_lamports() < stake + TX_FEE:
                    continue
                orders.append({"side": "buy", "mint": mint, "quote_lamports": stake,
                               "max_slippage_bps": p["buy_slip"]})
                self.entries[mint] += 1
                self.bucket_entries += 1
                committed += 1
                exposure += stake

        # ---- pyramid: add once to a held whale position that is proving itself ----
        if p.get("pyramid_add", 0) > 0 and len(orders) < 14:
            for mint, h in list(self.held.items()):
                if h.get("added") or h["scout"] or h["exiting"]:
                    continue
                pos = state.positions.get(mint)
                curve = state.curves.get(mint)
                if pos is None or curve is None or pos.cost_lamports <= 0:
                    continue
                val = close_value(pos.token_amount, curve.virtual_token_reserves, curve.virtual_quote_reserves)
                if val < pos.cost_lamports * (1 + p.get("pyramid_trigger", 0.15)):
                    continue
                w30 = self._window(mint, 30, slot)
                if not any(x[2] and x[3] >= p["whale_buy"] for x in w30[-6:]):
                    continue
                add = min(p["pyramid_add"], state.max_buy_lamports(mint))
                if add < p["min_cap"]:
                    continue
                if exposure + add > p["max_exposure"] * 2:
                    continue
                if state.free_cash_lamports() < add + TX_FEE:
                    continue
                orders.append({"side": "buy", "mint": mint, "quote_lamports": add,
                               "max_slippage_bps": p["buy_slip"]})
                h["added"] = True
                exposure += add

        # ---- scout sleeve (guardrail pacing) ----
        if not in_guard and self.t0 is not None:
            elapsed_h = (self.now - self.t0) / 3600.0
            need = min(p["scout_coins_target"], int(elapsed_h * p["scout_per_hour"]) + 1)
            have = len(self.trip_coins) + (1 if self.scout_mint else 0)
            if have < need and self.scout_mint is None and committed < p["max_positions"] \
                    and len(orders) < 14:
                # most liquid active curve not held and not already counted
                best = None; best_rq = -1
                for e in batch["events"]:
                    if e["event"] != "TradeEvent":
                        continue
                    mint = e["mint"]
                    if mint in state.positions or mint in self.trip_coins or mint == self.scout_mint:
                        continue
                    if any(pp.order["mint"] == mint for pp in state.pending.values()):
                        continue
                    curve = state.curves.get(mint)
                    if curve is None or curve.complete:
                        continue
                    if curve.real_quote_reserves > best_rq and state.max_buy_lamports(mint) >= p["scout_stake"]:
                        best = mint; best_rq = curve.real_quote_reserves
                if best is not None and p["rq_min"] <= best_rq <= p["rq_max"]:
                    orders.append({"side": "buy", "mint": best, "quote_lamports": p["scout_stake"],
                                   "max_slippage_bps": p["buy_slip"]})
                    self.scout_mint = best
                    self.entries[best] += 1
        return orders


def make_on_batch(params=None):
    s = Strat(params)
    return s.on_batch


# ---- competition entry point ----
# Full final configuration (validated in the offline replay sweeps; see
# candidates.md). Passed explicitly - the class DEFAULTS are the early
# research settings and must not leak into the graded run.
_STRAT = Strat({
    "entry_mode": "whale",          # whale-follow sleeve only
    "whale_buy": 5_000_000_000,     # single recorded buy >= 5 SOL
    "whale_lookback": 3,            # ... within the last 3 tape entries
    "net30_min": 500_000_000,       # net 30-slot flow >= +0.5 SOL
    "min_buyers30": 2,              # >= 2 distinct buyers in 30 slots
    "time_exit": 300,               # fixed 300-slot hold
    "stop": 0.10,                   # -10% liquidation-value stop
    "tp": 9.0,                      # no profit target while time_exit is on
    "trail_arm": 9.0,               # trailing disabled while time_exit is on
    "stake": 300_000_000,           # 0.30 SOL target stake (size cap applies)
    "max_exposure": 2_100_000_000,  # <= 2.1 SOL committed across positions
})

if __name__ == "__main__":
    _makefun_bot.run(_STRAT.on_batch)
