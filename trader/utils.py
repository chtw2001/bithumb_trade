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
