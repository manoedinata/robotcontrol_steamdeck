import asyncio
import logging
from typing import Any

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer


class WebRTCStream:
    """Create and clean up one RTSP-backed WebRTC peer per viewer."""

    def __init__(self, camera_url: str, logger: logging.Logger) -> None:
        self._camera_url = camera_url
        self._logger = logger
        self._sessions: dict[RTCPeerConnection, MediaPlayer] = {}
        self._lock = asyncio.Lock()

    async def update_url(self, camera_url: str) -> None:
        async with self._lock:
            self._camera_url = camera_url
            sessions = tuple(self._sessions)

        await asyncio.gather(*(self.close_peer(peer) for peer in sessions))

    async def create_answer(
        self, offer: RTCSessionDescription
    ) -> RTCSessionDescription:
        async with self._lock:
            camera_url = self._camera_url

        if not camera_url:
            raise ValueError("camera_url is not configured")

        peer = RTCPeerConnection()
        player: MediaPlayer | None = None
        try:
            player = MediaPlayer(
                camera_url,
                format="rtsp",
                options={
                    "rtsp_transport": "tcp",
                    "fflags": "nobuffer",
                    "flags": "low_delay",
                },
            )
            if player.video is None:
                raise RuntimeError("RTSP source does not provide a video track")

            peer.addTrack(player.video)

            @peer.on("connectionstatechange")
            async def on_connectionstatechange() -> None:
                if peer.connectionState in {"failed", "closed", "disconnected"}:
                    await self.close_peer(peer)

            await peer.setRemoteDescription(offer)
            answer = await peer.createAnswer()
            await peer.setLocalDescription(answer)

            async with self._lock:
                self._sessions[peer] = player
            return peer.localDescription
        except Exception:
            if player is not None:
                player.video and player.video.stop()
                player.audio and player.audio.stop()
            await peer.close()
            raise

    async def close_peer(self, peer: RTCPeerConnection) -> None:
        async with self._lock:
            player = self._sessions.pop(peer, None)

        if player is not None:
            if player.video is not None:
                player.video.stop()
            if player.audio is not None:
                player.audio.stop()
        if peer.connectionState != "closed":
            await peer.close()

    async def close(self) -> None:
        async with self._lock:
            peers = tuple(self._sessions)
        await asyncio.gather(*(self.close_peer(peer) for peer in peers))
