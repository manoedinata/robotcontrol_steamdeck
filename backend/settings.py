class Settings:
    def __init__(
        self,
        udp_ip: str = "127.0.0.1",
        udp_port: int = 8888,
        udp_listen_port: int = 8889,
        camera_streams: tuple[tuple[str, str], ...] = (),
        camera_backend: str = "go2rtc",
        ptz_ip: str = "",
        packet_slew: dict[str, float] | None = None,
    ):
        self.udp_ip = udp_ip
        self.udp_port = udp_port
        self.udp_listen_port = udp_listen_port
        # Every configured RTSP source is kept warm at once as (id, url) pairs
        # so the UI can switch between them without a reconnect. WebSocket
        # camera sources are not listed here; the renderer connects to those
        # directly.
        self.camera_streams = camera_streams
        self.camera_backend = camera_backend
        self.ptz_ip = ptz_ip
        # Operator overrides for the schema's per-field ramp rates, keyed by
        # field name. Empty means every field keeps the rate the schema
        # declares.
        self.packet_slew: dict[str, float] = dict(packet_slew or {})
