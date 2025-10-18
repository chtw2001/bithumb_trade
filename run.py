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
TICKER = os.getenv('TICKER')
# 매도 트리거 수익률(%) 기본 1.0% — 필요시 환경변수로 조정
TAKE_PROFIT_PCT = float(os.getenv('TAKE_PROFIT_PCT'))

bithumb = Bithumb(ACCESS_KEY, SECRET_KEY)


def sleep_until_epoch(target_epoch: float):
    """
    target_epoch(초, POSIX 시간)까지 짧게 끊어서 대기.
    드리프트를 최소화하기 위해 최대 0.5초 단위로 나눠 잔다.
    """
    while True:
        now = time.time()
        remaining = target_epoch - now
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))


if __name__ == '__main__':
    # 실행한 '현재 시각'을 기준으로 정확히 1시간 간격 유지
    # (정각 고정 아님: anchor + N*3600)
    anchor = time.time()
    next_run = anchor

    # 첫 실행 전 대기(START_DELAY_SEC가 0이면 즉시 실행)
    sleep_until_epoch(next_run)

    while True:
        try:
            trade_once(bithumb, TICKER, TAKE_PROFIT_PCT)
        except Exception as e:
            print(f"[FATAL] 라운드 에러: {e}")

        # 다음 슬롯 = 이전 기준시간 + 3600초
        next_run += 3600.0

        # 내부 처리(예: 5분 대기 등)로 늦어져 next_run이 과거라면
        # 누락된 슬롯을 건너뛰고 항상 '미래의 다음 슬롯'으로 보정
        now = time.time()
        while next_run <= now:
            next_run += 3600.0

        sleep_until_epoch(next_run)

'''
def sleep_until_next_top_of_hour():
    """
    다음 정각까지 대기. 내부에서 1초 단위 폴링으로 과도한 슬립에 따른 드리프트를 최소화.
    """
    while True:
        now = time.localtime()
        if now.tm_min == 0 and now.tm_sec < 2:
            # 정각 직후 즉시 실행(중복 방지용 2초 허용)
            return
        # 다음 체크까지 짧게 대기
        time.sleep(0.5)


if __name__ == '__main__':
    while True:
        sleep_until_next_top_of_hour()
        try:
            trade_once(bithumb, TICKER, TAKE_PROFIT_PCT)
        except Exception as e:
            print(f"[FATAL] 라운드 에러: {e}")
        # 라운드가 끝났더라도 다음 정각까지 대기하여 '1시간에 1번'을 유지
        # (5분 대기 등은 내부에서 처리되고, 여기선 정각 동기만 맞춤)
'''
