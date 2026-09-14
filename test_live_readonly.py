"""Smoke test read-only tren session ETABS dang mo. Khong sua doi mo hinh."""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_bridge import EtabsDataBridge
from connection import ensure_unlocked, ModelLockedError


def main():
    t = time.time()
    bridge = EtabsDataBridge()
    sap = bridge.sap_model
    print("Model:", sap.GetModelFilename())
    locked = bridge.is_locked()
    print("Locked:", locked)

    for name in ("pull_frames", "pull_shells", "pull_points"):
        df = getattr(bridge, name)()
        assert len(df) > 0, f"{name} rong"
        print(f"{name}: {len(df)} rows, cols={list(df.columns)[:5]}")

    stories = sap.Story.GetNameList()[1]
    assert len(stories) > 0
    print("Stories:", len(stories))

    try:
        ensure_unlocked(sap)
        assert not locked, "ensure_unlocked phai nem loi khi model bi khoa"
    except ModelLockedError:
        assert locked
    print("ensure_unlocked: OK")

    import __init__ as pkg  # kiem tra API export
    for n in pkg.__all__:
        assert hasattr(pkg, n), n
    print(f"__all__ OK ({len(pkg.__all__)} names). ALL PASSED in {time.time()-t:.1f}s")


if __name__ == "__main__":
    main()
