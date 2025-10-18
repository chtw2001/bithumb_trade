# 디렉토리 구조 예시:
# /trader/
#   __init__.py
#   strategy.py
#   utils.py
# run.py
# test_buy_sell.py   ← 요청하신 5천원 매수 & 5천원 매도 스모크 테스트 스크립트 (신규)

# =========================
# trader/utils.py
# =========================
from typing import Callable, Any, Optional
import time
import math
from python_bithumb import Bithumb

FEE_RATE = 0.0004  # 0.04%

class RetryError(Exception):
    pass

def retry(fn: Callable[[], Any], tries: int = 3, delay: float = 0.5, backoff: float = 2.0) -> Any:
    """
    간단한 재시도 유틸리티. 네트워크/일시적 오류에 대비.
    - tries: 총 시도 횟수
    - delay: 최초 대기 시간(초)
    - backoff: 지수 증가 배수
    """
    last_err: Optional[Exception] = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if i == tries - 1:
                break
            time.sleep(delay)
            delay *= backoff
    raise RetryError(last_err)


def get_order_unit(price: float) -> float:
    """
    가격대별 호가단위(티크) 계산. 지정가를 한 틱 낮출 때 사용.
    (거래소별 정책 차이가 있을 수 있으므로 필요 시 조정)
    """
    if price >= 2_000_000:
        return 1000
    elif price >= 1_000_000:
        return 500
    elif price >= 500_000:
        return 100
    elif price >= 100_000:
        return 50
    elif price >= 10_000:
        return 10
    elif price >= 1_000:
        return 5
    elif price >= 100:
        return 1
    elif price >= 10:
        return 0.1
    elif price >= 1:
        return 0.01
    else:
        return 0.001


def round_down(x: float, unit: float) -> float:
    """호가단위에 맞춰 내림 반올림."""
    if unit == 0:
        return x
    k = math.floor(x / unit)
    return round(k * unit, 8)


def min_volume_for_krw(min_total_krw: float, price: float) -> float:
    """
    최소 주문 금액을 만족하기 위한 최소 수량 계산.
    수량은 소수 8자리로 제한(일반적 관행)합니다.
    """
    if price <= 0:
        return 0.0
    return round(min_total_krw / price, 8)


def effective_pnl_pct(current_price: float, avg_price: float, fee_rate: float = FEE_RATE) -> float:
    """
    수수료(매수·매도 각각 fee_rate)를 반영한 실현가능 수익률(%)을 계산.
    = [현재가*(1-fee) - 평단*(1+fee)] / [평단*(1+fee)] * 100
    """
    if avg_price <= 0:
        return 0.0
    numerator = current_price * (1 - fee_rate) - avg_price * (1 + fee_rate)
    denom = avg_price * (1 + fee_rate)
    return (numerator / denom) * 100.0


def is_order_fully_done(bithumb: Bithumb, uuid: str) -> bool:
    """주문 상태가 done(완료)인지 확인."""
    def _get():
        return bithumb.get_order(uuid)
    data = retry(_get)
    return str(data.get('state')) == 'done'


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


# =========================
# run.py
# =========================
import os
import time
from python_bithumb import Bithumb
from trader.strategy import trade_once

from dotenv import load_dotenv
load_dotenv()
# 환경 변수로 키/설정 로딩 (없으면 직접 문자열 입력)
ACCESS_KEY = os.getenv('BITHUMB_ACCESS_KEY')
SECRET_KEY = os.getenv('BITHUMB_SECRET_KEY')
TICKER = os.getenv('TICKER') or 'KRW-BTC'
# 매도 트리거 수익률(%) 기본 1.0% — 필요시 환경변수로 조정
TAKE_PROFIT_PCT = float(os.getenv('TAKE_PROFIT_PCT'))
# 시작 지연(초). 예: 120이면 실행 후 2분 뒤를 첫 기준으로 사용
START_DELAY_SEC = 0

bithumb = Bithumb(ACCESS_KEY, SECRET_KEY)


def sleep_until_epoch(target_epoch: float):
    """POSIX epoch 시각까지 짧게 분할해 대기(드리프트 최소화)."""
    while True:
        now = time.time()
        remaining = target_epoch - now
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))

if __name__ == '__main__':
    # 실행한 '현재 시각'을 기준으로 정확히 1시간 간격 유지
    anchor = time.time() + START_DELAY_SEC
    next_run = anchor

    # 첫 실행 전 대기(START_DELAY_SEC가 0이면 즉시 실행)
    sleep_until_epoch(next_run)

    while True:
        try:
            trade_once(bithumb, TICKER, TAKE_PROFIT_PCT)
        except Exception as e:
            print(f"[FATAL] 라운드 에러: {e}")
        quit()
        # 다음 슬롯 = 이전 기준시간 + 3600초 (드리프트 방지)
        next_run += 3600.0
        now = time.time()
        while next_run <= now:
            next_run += 3600.0
        sleep_until_epoch(next_run)

# =========================
# test_buy_sell.py (신규)
# =========================
"""
요청하신 스모크 테스트:
- 시장가로 5,000원어치 매수
- 곧바로 5,000원어치에 해당하는 수량을 시장가로 매도
현재 저장소 코드(python_bithumb, trader.utils)만 사용합니다.
안전장치: 최소주문금액/수량, 가용잔고, 재시도 등 체크.
"""
import os
import time
from python_bithumb import Bithumb, get_current_price
from trader.utils import retry, min_volume_for_krw, FEE_RATE

AMOUNT_KRW = float(os.getenv('TEST_AMOUNT_KRW') or 5000.0)
TICKER = os.getenv('TICKER') or 'KRW-BTC'
ACCESS_KEY = os.getenv('BITHUMB_ACCESS_KEY') or 'YOUR_ACCESS_KEY'
SECRET_KEY = os.getenv('BITHUMB_SECRET_KEY') or 'YOUR_SECRET_KEY'


def preflight(client: Bithumb, ticker: str):
    # Public
    cp = retry(lambda: get_current_price(ticker))
    print(f"[PREFLIGHT] 현재가: {ticker} -> {cp}")

    # Private
    ch = retry(lambda: client.get_order_chance(ticker))
    bid_min = float(ch.get('bid_account', {}).get('min_total', 0))
    ask_min = float(ch.get('ask_account', {}).get('min_total', 0))
    krw_avail = float(ch.get('bid_account', {}).get('balance', 0))
    coin_avail = float(ch.get('ask_account', {}).get('balance', 0))
    print(f"[PREFLIGHT] bid_min={bid_min}, ask_min={ask_min}, KRW_avail={krw_avail}, COIN_avail={coin_avail}")
    return cp, bid_min, ask_min, krw_avail, coin_avail


def buy_krw(client: Bithumb, ticker: str, krw_amount: float, bid_min: float, krw_avail: float):
    if krw_amount < bid_min:
        raise RuntimeError(f"요청 매수 금액({krw_amount})이 최소 주문 금액({bid_min}) 미만")
    if krw_avail < krw_amount:
        raise RuntimeError(f"KRW 가용잔고 부족: avail={krw_avail}, need={krw_amount}")
    resp = retry(lambda: client.buy_market_order(ticker, krw_amount))
    print(f"[BUY] 시장가 매수 제출: {krw_amount}원 → 응답: {resp}")
    return resp


def sell_krw_equivalent(client: Bithumb, ticker: str, target_krw: float, ask_min: float):
    # 현재가로 환산해 수량 계산
    cp = retry(lambda: get_current_price(ticker))
    vol = round(target_krw / max(cp, 1e-12), 8)

    # 가용 수량 및 최소 주문 금액 충족 보정
    ch2 = retry(lambda: client.get_order_chance(ticker))
    coin_avail = float(ch2.get('ask_account', {}).get('balance', 0))

    if vol > coin_avail:
        print(f"[SELL] 가용 수량 부족. 보유={coin_avail}, 요구={vol} → 보유치로 축소")
        vol = round(coin_avail, 8)

    est_total = vol * cp
    if est_total < ask_min:
        # 최소 주문 금액을 만족하도록 수량 상향 (보유량 내)
        need_vol = min_volume_for_krw(ask_min, cp)
        if need_vol <= coin_avail:
            print(f"[SELL] 최소주문금액 보정: {vol} → {need_vol}")
            vol = round(need_vol, 8)
            est_total = vol * cp
        else:
            raise RuntimeError(f"매도 최소금액 미달: 추정 {est_total:.0f} < 최소 {ask_min:.0f}, 보유 {coin_avail}로도 충족 불가")

    resp = retry(lambda: client.sell_market_order(ticker, vol))
    print(f"[SELL] 시장가 매도 제출: vol={vol} (≈{est_total:.0f}원) → 응답: {resp}")
    return resp


def main():
    # 키 검증
    if ACCESS_KEY.startswith('YOUR_') or SECRET_KEY.startswith('YOUR_'):
        raise SystemExit("[CONFIG] API 키가 설정되지 않았습니다. 환경변수 또는 파일에 실제 키를 넣어주세요.")

    client = Bithumb(ACCESS_KEY, SECRET_KEY)

    # 점검
    cp, bid_min, ask_min, krw_avail, coin_avail = preflight(client, TICKER)

    # 1) 시장가 5천원 매수
    buy_krw(client, TICKER, AMOUNT_KRW, bid_min, krw_avail)

    # 체결/잔고 반영 대기 (환경에 따라 조정)
    time.sleep(3)

    # 2) 방금 산 것 포함해서 현재가 기준 5천원어치 매도
    sell_krw_equivalent(client, TICKER, AMOUNT_KRW, ask_min)

    print("[DONE] 5천원 매수 → 5천원어치 매도 스모크 테스트 완료")


if __name__ == '__main__':
    main()
