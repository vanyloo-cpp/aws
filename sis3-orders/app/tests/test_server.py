import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import Mock

from botocore.exceptions import ClientError
from server import OrdersHandler


class OrdersApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = ThreadingHTTPServer(("127.0.0.1", 0), OrdersHandler)
        cls.api.daemon_threads = True
        cls.api.bus_name = "test-orders"
        cls.api.events = Mock()
        cls.thread = threading.Thread(target=cls.api.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.api.shutdown()
        cls.api.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.api.events.reset_mock(return_value=True, side_effect=True)
        self.api.events.put_events.return_value = {
            "FailedEntryCount": 0,
            "Entries": [{"EventId": "event-123"}],
        }

    def request(self, method, path, payload=None):
        body = json.dumps(payload) if payload is not None else None
        connection = HTTPConnection(*self.api.server_address, timeout=5)
        try:
            connection.request(
                method, path, body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_health(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"status": "ok"}))
        self.api.events.put_events.assert_not_called()

    def test_unknown_route(self):
        status, _ = self.request("GET", "/missing")
        self.assertEqual(status, 404)

    def test_order_is_published(self):
        status, payload = self.request("POST", "/orders", {"orderId": "order-123"})
        self.assertEqual(status, 202)
        self.assertEqual(payload, {"orderId": "order-123", "eventId": "event-123"})
        self.api.events.put_events.assert_called_once()
        entry = self.api.events.put_events.call_args.kwargs["Entries"][0]
        self.assertEqual(entry["EventBusName"], "test-orders")
        self.assertEqual(entry["Source"], "sis3.orders")
        self.assertEqual(entry["DetailType"], "OrderCreated")
        self.assertEqual(json.loads(entry["Detail"]), {"orderId": "order-123"})

    def test_invalid_order_ids_are_rejected(self):
        for order_id in ("", "with spaces", "x" * 65, 123):
            with self.subTest(order_id=order_id):
                status, _ = self.request("POST", "/orders", {"orderId": order_id})
                self.assertEqual(status, 400)
        self.api.events.put_events.assert_not_called()

    def test_missing_order_id(self):
        status, _ = self.request("POST", "/orders", {})
        self.assertEqual(status, 400)
        self.api.events.put_events.assert_not_called()

    def test_eventbridge_rejection_returns_503(self):
        self.api.events.put_events.return_value = {
            "FailedEntryCount": 1,
            "Entries": [{"ErrorCode": "InternalFailure"}],
        }
        status, _ = self.request("POST", "/orders", {"orderId": "order-123"})
        self.assertEqual(status, 503)

    def test_aws_error_returns_503(self):
        self.api.events.put_events.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Denied"}},
            "PutEvents",
        )
        status, _ = self.request("POST", "/orders", {"orderId": "order-123"})
        self.assertEqual(status, 503)


if __name__ == "__main__":
    unittest.main()
