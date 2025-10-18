# =========================
# trader/strategy.py
# =========================
import time
from typing import Tuple
from python_bithumb import Bithumb, get_current_price
from trader.utils import (
    get_order_unit,
    round_down,
    min_volume_for_krw,
    is_order_fully_done,
    effective_pnl_pct,
    FEE_RATE,
    retry,
)

MIN_ORDER_KRW_DEFAULT = 5000.0
VOLUME_DECIMALS = 8


def _fetch_chance_safe(bithumb: Bithumb, ticker: str) -> dict:
    return retry(lambda: bithumb.get_order_chance(ticker))


def _balances_safe(bithumb: Bithumb) -> Tuple[float, float]:
    """KRW, 코인 보유량(주문가능) 반환."""
    balances = retry(lambda: bithumb.get_balances())
    krw_avail = 0.0
    coin_avail = 0.0
    for bal in balances:
        cur = bal.get('currency')
        if cur == 'KRW':
            krw_avail = float(bal.get('balance', 0))
        # 실제 심볼은 마켓에서 분리해 가져오는 쪽에서 처리
    return krw_avail, coin_avail


def _get_coin_available_from_chance(chance: dict, side: str) -> float:
    # chance 응답의 계정 가용잔고를 사용 (락된 잔고 제외)
    # side: 'bid'면 bid_account, 'ask'면 ask_account
    key = 'bid_account' if side == 'bid' else 'ask_account'
    acc = chance.get(key, {})
    return float(acc.get('balance', 0))


def perform_buy(bithumb: Bithumb, ticker: str) -> None:
    """
    매수 전략:
    - 현재가 조회
    - chance로 min_total(최소 주문 금액), avg_buy_price(평단) 확인
    - 보유 여부에 따라 5천/1만원 결정
    - 지정가(현재가 - 1틱) 5분 대기 → 미체결 시 시장가 재매수
    """
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    cur_price = retry(lambda: get_current_price(ticker))
    chance = _fetch_chance_safe(bithumb, ticker)

    min_total = float(chance.get('bid_account', {}).get('min_total', MIN_ORDER_KRW_DEFAULT))
    krw_avail = float(chance.get('bid_account', {}).get('balance', 0.0))  # 사용가능 KRW

    coin_symbol = ticker.split('-')[1]
    avg_buy_price = float(chance.get('ask_account', {}).get('avg_buy_price', 0.0))
    coin_avail_for_sell = float(chance.get('ask_account', {}).get('balance', 0.0))

    if krw_avail < min_total:
        print(f"[{now}] 매수 스킵: KRW 잔고 부족 (avail={krw_avail:.0f}, min={min_total:.0f})")
        return

    # 금액 결정
    amount = 5000.0
    if coin_avail_for_sell > 0:
        if avg_buy_price < cur_price:
            amount = 10000.0
        else:
            amount = 5000.0

    # 지정가 가격: 현재가에서 한 틱 낮춤(내림 처리)
    tick = get_order_unit(cur_price)
    limit_price = round_down(cur_price - tick, tick)

    # 최소 주문 금액 보장 및 수량 계산
    # 체결가가 limit_price 기준이라고 가정해 계산함
    volume = round(amount / max(limit_price, 1e-12), VOLUME_DECIMALS)

    # 지정가 주문 → 5분 대기 → 미체결 시 취소 후 시장가
    try:
        order = retry(lambda: bithumb.buy_limit_order(ticker, limit_price, volume))
        uuid = order.get('uuid')
        print(f"[{now}] 지정가 매수 주문 제출: {ticker} price={limit_price} vol={volume} uuid={uuid}")
        time.sleep(300)  # 5분 대기
        if not is_order_fully_done(bithumb, uuid):
            retry(lambda: bithumb.cancel_order(uuid))
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 미체결 → 시장가 매수 재주문: {amount}")
            retry(lambda: bithumb.buy_market_order(ticker, amount))
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 매수 체결 완료: uuid={uuid}")
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 매수 에러: {e}")


def perform_sell(bithumb: Bithumb, ticker: str, take_profit_pct: float) -> None:
    """
    매도 전략(매 시간 1회 체크):
    - 현재가, 평단 조회
    - 수수료 반영 실질 수익률이 take_profit_pct 이상이면 보유량의 10% 시장가 매도
    - 최소 주문 금액/수량 충족 확인
    """
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    cur_price = retry(lambda: get_current_price(ticker))
    chance = _fetch_chance_safe(bithumb, ticker)

    avg_buy_price = float(chance.get('ask_account', {}).get('avg_buy_price', 0.0))
    coin_balance = float(chance.get('ask_account', {}).get('balance', 0.0))  # 사용가능 수량
    min_total = float(chance.get('ask_account', {}).get('min_total', MIN_ORDER_KRW_DEFAULT))

    if coin_balance <= 0 or avg_buy_price <= 0:
        print(f"[{now}] 매도 스킵: 보유 없음 또는 평단 0 (bal={coin_balance}, avg={avg_buy_price})")
        return

    pnl_pct = effective_pnl_pct(cur_price, avg_buy_price, FEE_RATE)
    if pnl_pct < take_profit_pct:
        print(f"[{now}] 매도 스킵: 목표 미충족 (pnl={pnl_pct:.3f}%, target={take_profit_pct:.3f}%)")
        return

    # 매도 수량 = 보유량의 10%
    sell_volume = round(coin_balance * 0.10, VOLUME_DECIMALS)

    # 최소 주문 금액 기준 체크 (시장가 매도라도 체결가 기준 min_total 요구가 있는 경우 대비)
    est_total = sell_volume * cur_price
    if est_total < min_total:
        # 가능한 최대치로 늘려서 최소 충족 여부 재확인 (보유량 내)
        min_vol = min_volume_for_krw(min_total, cur_price)
        if min_vol <= coin_balance:
            sell_volume = round(min_vol, VOLUME_DECIMALS)
            est_total = sell_volume * cur_price
        else:
            print(f"[{now}] 매도 스킵: 최소 금액 미달 (est={est_total:.0f} < min={min_total:.0f})")
            return

    try:
        order = retry(lambda: bithumb.sell_market_order(ticker, sell_volume))
        uuid = order.get('uuid')
        print(f"[{now}] 시장가 매도 주문 제출: {ticker} vol={sell_volume} uuid={uuid} (pnl={pnl_pct:.3f}%)")
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 매도 에러: {e}")


def trade_once(bithumb: Bithumb, ticker: str, take_profit_pct: float) -> None:
    """한 번의 라운드(한 시간 슬롯)에서 매도→매수 순서로 실행."""
    print(f"==== {time.strftime('%Y-%m-%d %H:%M:%S')} {ticker} 라운드 시작 ====")
    perform_sell(bithumb, ticker, take_profit_pct)
    perform_buy(bithumb, ticker)
    print(f"==== {time.strftime('%Y-%m-%d %H:%M:%S')} {ticker} 라운드 종료 ====")
