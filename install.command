#!/bin/sh
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$TASK_ROOT"
"$TASK_ROOT/xiaod" doctor
printf '%s\n' '将安装基础聊天、MiMo 语音和完整唱歌环境；模型下载约数GB，不训练。'
"$TASK_ROOT/xiaod" install --singing
"$TASK_ROOT/xiaod" init
printf '%s\n' '配置完成。运行 ./xiaod start，再用托管QQ扫码登录。'
