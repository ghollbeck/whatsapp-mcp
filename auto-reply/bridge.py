# ABOUTME: Async HTTP client for the Go WhatsApp bridge REST API.
# ABOUTME: Sends text messages, files, and audio through the bridge on port 8082.

import asyncio
import aiohttp
import structlog

logger = structlog.get_logger("bridge")


class BridgeClient:
    def __init__(self, base_url: str = "http://localhost:8082/api", timeout: int = 10):
        self.base_url = base_url
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def send_message(self, recipient: str, message: str) -> tuple[bool, str]:
        url = f"{self.base_url}/send"
        payload = {"recipient": recipient, "message": message}

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        success = data.get("success", False)
                        msg = data.get("message", "Unknown response")
                        if success:
                            logger.info("message_sent", recipient=recipient, length=len(message))
                        else:
                            logger.error("message_send_failed", recipient=recipient, error=msg)
                        return success, msg
                    else:
                        text = await resp.text()
                        logger.error("bridge_http_error", status=resp.status, body=text[:200])
                        return False, f"HTTP {resp.status}: {text}"

        except aiohttp.ClientConnectorError:
            logger.error("bridge_connection_failed", url=url)
            return False, "Cannot connect to Go bridge. Is it running?"
        except Exception as e:
            logger.error("bridge_unexpected_error", error=str(e))
            return False, f"Unexpected error: {e}"

    async def send_file(self, recipient: str, file_path: str, caption: str = "") -> tuple[bool, str]:
        url = f"{self.base_url}/send"
        payload = {"recipient": recipient, "message": caption, "media_path": file_path}

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        success = data.get("success", False)
                        msg = data.get("message", "")
                        if success:
                            logger.info("file_sent", recipient=recipient, path=file_path)
                        return success, msg
                    else:
                        text = await resp.text()
                        return False, f"HTTP {resp.status}: {text}"
        except Exception as e:
            logger.error("file_send_error", error=str(e))
            return False, str(e)

    async def send_chunked(self, recipient: str, chunks: list[str],
                           delay: float = 0.5) -> list[tuple[bool, str]]:
        results = []
        for i, chunk in enumerate(chunks):
            success, msg = await self.send_message(recipient, chunk)
            results.append((success, msg))
            if not success:
                logger.error("chunked_send_failed", chunk_index=i, error=msg)
                break
            if i < len(chunks) - 1:
                await asyncio.sleep(delay)
        return results

    async def health_check(self) -> bool:
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(f"{self.base_url}/send", json={}) as resp:
                    return resp.status in (200, 400, 405)
        except Exception:
            return False
