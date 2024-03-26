import asyncio
import base64

from typing import AsyncGenerator, Dict, Optional
from websockets.client import connect

import httpx
from loguru import logger

from lnbits import bolt11
from lnbits.settings import settings

from .base import (
    InvoiceResponse,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    StatusResponse,
    Unsupported,
    Wallet,
)


class PhoenixdWallet(Wallet):
    """https://phoenix.acinq.co/server/api"""

    def __init__(self):
        if not settings.phoenixd_api_endpoint:
            raise ValueError("cannot initialize PhoenixdWallet: missing phoenixd_api_endpoint")
        if not settings.phoenixd_api_password:
            raise ValueError("cannot initialize PhoenixdWallet: missing phoenixd_api_password")

        self.endpoint = self.normalize_endpoint(settings.phoenixd_api_endpoint)
        self.client = httpx.AsyncClient(base_url=self.endpoint, auth=('', settings.phoenixd_api_password))

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    async def status(self) -> StatusResponse:
        try:
            r = await self.client.get("/getinfo", timeout=10)
        except (httpx.ConnectError, httpx.RequestError):
            return StatusResponse(f"Unable to connect to '{self.endpoint}'", 0)

        if r.is_error:
            error_message = r.json()["message"]
            return StatusResponse(error_message, 0)

        data = int(r.json()['channels'][0]['balanceSat'])*1000
        return StatusResponse(None, data)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        if description_hash or unhashed_description:
            raise Unsupported("description_hash")

        msats_amount = amount
        data: Dict = {
            "amountSat": f"{msats_amount}",
            "description": memo,
            "externalId": "",
        }

        r = await self.client.post(
            "/createinvoice",
            data=data,
            timeout=40,
        )

        if r.is_error:
            error_message = r.json()["message"]
            return InvoiceResponse(False, None, None, error_message)

        data = r.json()
        # logger.info(f'data: {data}')

        checking_id = data["paymentHash"]
        payment_request = data["serialized"]
        return InvoiceResponse(True, checking_id, payment_request, None)

    async def pay_invoice(
        self, bolt11_invoice: str, fee_limit_msat: int
    ) -> PaymentResponse:
        r = await self.client.post(
            "/payinvoice",
            json={
                "invoice": bolt11_invoice,
                "amountSat": '1',
            },
            timeout=40,
        )

        if r.is_error:
            error_message = r.json()["message"]
            return PaymentResponse(False, None, None, None, error_message)

        data = r.json()
        logger.info(f'data: {data}')

        checking_id = data['paymentHash']
        fee_msat = -int(data['routingFeeSat'])
        preimage = data["paymentPreimage"]

        return PaymentResponse(True, checking_id, fee_msat, preimage, None)

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        r = await self.client.get(f"/payments/incoming/{checking_id}")
        if r.is_error:
            return PaymentPendingStatus()
        data = r.json()
        logger.info(f'data: {data}')

        fee_msat = data['fees']
        preimage= data["preimage"]
        is_paid = data['isPaid']

        return PaymentStatus(paid=is_paid, fee_msat=fee_msat, preimage=preimage)


    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        return await self.get_invoice_status(checking_id)


    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        self.queue: asyncio.Queue = asyncio.Queue(0)
        while True:
            value = await self.queue.get()
            yield value

    # async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
    #     while True:
    #         try:
    #             async with connect(
    #                 "ws://127.0.0.1:9740/websocket",
    #                 # TODO: fix this, not connecting
    #                 # extra_headers=[("", settings.phoenixd_api_password)],
    #             ) as ws:
    #                 while True:
    #                     message = await ws.recv()
    #                     logger.info(f'message: {message}')
    #                     # message_json = json.loads(message)
    #                     # if message_json and message_json["type"] == "payment-received":
    #                     #     yield message_json["paymentHash"]
    #                     if message:
    #                         yield message

    #         except Exception as exc:
    #             logger.error(
    #                 f"lost connection to phoenixd invoices stream: '{exc}'"
    #                 "retrying in 5 seconds"
    #             )
    #             await asyncio.sleep(5)
