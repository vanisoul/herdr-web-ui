"""Fail closed before starting the loopback backend."""
import os
import sys

if os.environ.get("HOST") != "127.0.0.1" or len(os.environ.get("HERDR_WEB_TOKEN", "")) < 32:
    sys.exit("拒絕啟動：必須綁定 loopback 並設定至少 32 個字元的 Token")
