#!/usr/bin/env bash
# strict mode 없이 동작하는 버전

PY="/usr/bin/python3"
PY_ARGS="-u"
SCRIPT="/home/taekwon/Documents/bithumb/run.py"

# 경로 점검
if [ ! -x "$PY" ]; then
  echo "[WARN] Python 경로가 실행 불가: $PY"
  exit 1
fi
if [ ! -f "$SCRIPT" ]; then
  echo "[WARN] 실행 스크립트가 없습니다: $SCRIPT"
  exit 1
fi

# 예: 25.10.18 22:21
TS="$(date +'%y.%m.%d %H:%M')"

LOG_DIR="/home/taekwon/Documents/bithumb/logs/"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$TS"         # 공백/콜론 포함 → 항상 따옴표로 감싸서 사용

# 백그라운드 실행 (unbuffered)
nohup "$PY" $PY_ARGS "$SCRIPT" >> "$LOG_FILE" 2>&1 &

echo "started PID=$!"
echo "log: $LOG_FILE"
