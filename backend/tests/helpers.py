import io

import numpy as np

SR = 16000


def make_webm(seconds: float = 1.0, freq: float = 440.0) -> bytes:
    """Encode a real WebM/Opus file with PyAV (no model or network needed)."""
    import av

    buf = io.BytesIO()
    container = av.open(buf, mode="w", format="webm")
    stream = container.add_stream("libopus", rate=48000)
    stream.layout = "mono"
    n = int(48000 * seconds)
    pcm = (0.3 * np.sin(2 * np.pi * freq * np.arange(n) / 48000)).astype(np.float32)
    frame_size, pts = 960, 0
    for i in range(0, n - frame_size + 1, frame_size):
        frame = av.AudioFrame.from_ndarray(pcm[i : i + frame_size].reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = 48000
        frame.pts = pts
        pts += frame_size
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    return buf.getvalue()


# Minimal bytes that pass the container sniff; fakes below never really decode them.
FAKE_WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
