from router.hardware import gpu_metrics
from router.monitor import parse_gauges


def test_gpu_export_omits_missing_values_and_preserves_devices():
    gauges = parse_gauges(
        "\n".join(gpu_metrics("0, 90, 22000, 2576, 65, 240.2\n1, 0, 18, 24558, [N/A], [N/A]"))
    )
    assert [v["labels"]["gpu"] for v in gauges["DCGM_FI_DEV_GPU_UTIL"]] == ["0", "1"]
    assert gauges["DCGM_FI_DEV_FB_USED"][0]["value"] == 22000
    assert len(gauges["DCGM_FI_DEV_GPU_TEMP"]) == 1
    assert gpu_metrics("0, nan, inf, N/A, N/A, N/A") == []
