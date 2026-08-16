class Settings:
    def __init__(
        self,
        udp_ip: str = "127.0.0.1",
        udp_port: int = 8888,
        rtsp_url: str = "rtsp://admin:password@127.0.0.1:554/stream",
    ):
        self.udp_ip = udp_ip
        self.udp_port = udp_port
        self.rtsp_url = rtsp_url
