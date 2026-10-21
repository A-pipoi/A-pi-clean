import pytest

from quant.api.feature_materialization import (
    FEATURE_MATERIALIZATION_ERROR_CODES,
    classify_materialization_error,
)
from quant.api.resource_limits import ResourceLimitExceeded
from quant.errors import ProviderShapeError

CASES = [
    (RuntimeError("未配置 TUSHARE_TOKEN，无法加载历史指数成分序列"), "tushare_token_missing", 503),
    (ValueError("PIT 特征宇宙需要 900 只股票，超过安全上限 800"), "universe_too_large", 400),
    (ValueError("单次特征库预热最多覆盖5年；请缩短区间"), "range_too_long", 400),
    (RuntimeError("PIT特征库预热行情完整性不足：股票池共 300 只，仅返回 298 只；缺少 600000.SH"), "incomplete_universe", 502),
    (RuntimeError("PIT 特征库预热只接受本次真实 Tushare 拉取，不接受缓存或混合行情"), "data_source_not_live", 502),
    (RuntimeError("特征行情落后于真实数据探针：20261010 < 20261014"), "data_stale", 502),
    (TimeoutError("read timed out"), "tushare_timeout", 504),
    (TimeoutError("timed out"), "tushare_timeout", 504),
    (RuntimeError("Tushare 未返回可用历史行情；600000.SH: ReadTimeout"), "tushare_timeout", 504),
    (ConnectionError("Max retries exceeded"), "tushare_network", 502),
    (ResourceLimitExceeded("tushare hourly budget exhausted", retry_after=30), "quota", 429),
    (Exception("抱歉，您每分钟最多访问该接口500次"), "quota", 429),
    (ProviderShapeError("PIT 特征宇宙缺少数据拉取起始日期"), "provider_shape", 502),
    (RuntimeError("PIT 成分与真实日线交集为空，未写入特征库"), "provider_empty", 502),
    (ValueError("特征库开始日期不能晚于结束日期"), "invalid_feature_request", 400),
    (RuntimeError("something odd"), "feature_data_unavailable", 502),
    (KeyError("boom"), "unknown", 500),
]

@pytest.mark.parametrize("error,code,status", CASES)
def test_classification(error, code, status):
    result = classify_materialization_error(error, token="")
    assert result["code"] == code and result["status"] == status
    assert result["hint"] == FEATURE_MATERIALIZATION_ERROR_CODES[code][1]
    assert result["detail"] and result["detail"] in result["error"]

def test_original_message_kept_truncated_and_token_redacted():
    secret = "a1b2c3d4" * 7
    long_tail = "x" * 2000
    result = classify_materialization_error(RuntimeError(f"bad request token={secret} {secret} {long_tail}"), token=secret)
    assert secret not in result["error"] and secret not in result["detail"]
    assert "bad request" in result["detail"] and result["detail"].endswith("（已截断）")
    assert len(result["detail"]) < 600
