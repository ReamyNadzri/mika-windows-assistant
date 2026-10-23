import json
from io import BytesIO
from unittest.mock import patch

from hermes_personal.reviewer import UIReviewer


def test_text_only_model_never_receives_screenshot_and_requires_owner_approval():
    reviewer = UIReviewer("http://localhost:11434/v1", "local", "text-model", supports_vision=False)
    with patch("urllib.request.urlopen") as request:
        decision = reviewer.review("browser_click", {"ref": "button"}, "Open report", {"images": ["data:image/png;base64,private-image"]})
    assert decision["action"] == "approve" and "text-only" in decision["message"]
    request.assert_not_called()


def test_text_only_model_can_review_text_observations():
    reviewer = UIReviewer("http://localhost:11434/v1", "local", "text-model", supports_vision=False)
    response = BytesIO(json.dumps({"choices": [{"message": {"content": '{"allow": true}'}}]}).encode())
    with patch("urllib.request.urlopen", return_value=response) as request:
        decision = reviewer.review("browser_click", {"ref": "button"}, "Open report", {"text": "button Open report"})
    assert decision is None
    body = json.loads(request.call_args.args[0].data)
    assert set(body) == {"model", "messages", "max_tokens"}
    assert all(part["type"] == "text" for part in body["messages"][1]["content"])
