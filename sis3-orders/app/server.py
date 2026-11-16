import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

ORDER_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


class OrdersHandler(BaseHTTPRequestHandler):
    timeout = 10

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
        else:
            self.send_json(404, {"error": "Route not found"})

    def do_POST(self):
        if self.path != "/orders":
            self.send_json(404, {"error": "Route not found"})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Use Content-Type application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 4096:
                raise ValueError("Invalid body length")
            payload = json.loads(self.rfile.read(length))
            order_id = payload["orderId"]
            if not isinstance(order_id, str) or not ORDER_ID_PATTERN.fullmatch(order_id):
                raise ValueError("Invalid order ID")
        except (KeyError, TypeError, ValueError):
            self.send_json(400, {"error": "Supply orderId using 1–64 ASCII letters, digits, hyphens, or underscores"})
            return
        try:
            result = self.server.events.put_events(
                Entries=[{
                    "EventBusName": self.server.bus_name,
                    "Source": "sis3.orders",
                    "DetailType": "OrderCreated",
                    "Detail": json.dumps({"orderId": order_id}),
                }]
            )
            if result["FailedEntryCount"]:
                self.log_error("PutEvents rejected the order: %s", result["Entries"][0].get("ErrorCode"))
                self.send_json(503, {"error": "Could not publish order"})
                return
        except (BotoCoreError, ClientError) as error:
            self.log_error("PutEvents failed: %s", type(error).__name__)
            self.send_json(503, {"error": "Could not publish order"})
            return
        self.send_json(202, {"orderId": order_id, "eventId": result["Entries"][0]["EventId"]})

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    bus_name = os.environ["ORDERS_EVENT_BUS"]
    with ThreadingHTTPServer(("0.0.0.0", 8080), OrdersHandler) as server:
        server.bus_name = bus_name
        server.events = boto3.client(
            "events",
            config=Config(connect_timeout=2, read_timeout=4, retries={"total_max_attempts": 2}),
        )
        server.serve_forever()
