"""
Fetch the person re-identification weights Phase B needs.

Run once after cloning:  venv/Scripts/python.exe backend/models/download_models.py

WHY NOT COMMIT THE FILE

It is 3 MB of binary that never changes. Committing it puts it in every clone's
history forever; a 20-line fetcher does not.

WHICH WEIGHTS

`osnet_x0_25` fine-tuned on MSMT17 (1041 identities) from the torchreid model
zoo. NOT the ImageNet weights torchreid downloads by default — those describe
"what kind of object is this", and person re-ID needs "is this the same person".

Without this file the pipeline still runs: `appearance.py` logs a warning and
falls back to colour and height signals only, which is degraded but working.
"""
import os
import sys

DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "osnet_x0_25_msmt17.pth")

# torchreid model zoo, osnet_x0_25 / MSMT17.
GDRIVE_ID = "1sSwXSUlj4_tHZequ_iZ8w_Jh0VaRQMqF"
MIN_BYTES = 1_000_000


def main():
    if os.path.exists(DEST) and os.path.getsize(DEST) > MIN_BYTES:
        print(f"already present: {DEST} ({os.path.getsize(DEST) / 1e6:.1f} MB)")
        return 0
    try:
        import gdown
    except ImportError:
        print("gdown is required: pip install -r backend/requirements.txt")
        return 1

    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    print("downloading osnet_x0_25 (MSMT17) ...")
    gdown.download(id=GDRIVE_ID, output=DEST, quiet=False)

    if not os.path.exists(DEST) or os.path.getsize(DEST) < MIN_BYTES:
        print("download failed or truncated.")
        return 1
    print(f"saved {DEST} ({os.path.getsize(DEST) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
