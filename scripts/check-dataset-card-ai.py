"""Local dataset-card acceptance. --live-generate makes one paid provider call.

Use a synthetic acceptance dataset, not a private dataset. Provider credentials
are never read by this script: the isolated gateway owns them.
"""
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-id", required=True)
    parser.add_argument("--base-url", default="http://localhost:8001")
    parser.add_argument("--live-generate", action="store_true")
    args = parser.parse_args()
    url = args.base_url.rstrip("/") + "/api/dataset-versions/" + urllib.parse.quote(args.version_id, safe="") + "/card"

    def request(method="GET", body=None, suffix=""):
        req = urllib.request.Request(url + suffix, method=method, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    status, before = request()
    assert status == 200, f"GET failed: {status}"
    assert before["facts"]["classes"], "Need an imported dataset with real classes"
    status, _ = request("PUT", {"expected_revision": -1, "dataset_card": before["dataset_card"]})
    assert status == 422
    status, _ = request("PUT", {"expected_revision": before["revision"] + 10, "dataset_card": before["dataset_card"]})
    assert status == 409
    status, _ = request("POST", {}, "/generate")
    assert status == 422
    if args.live_generate:
        body = {"request_id": str(uuid.uuid4())}
        status, generated = request("POST", body, "/generate")
        assert status == 200, f"Generation failed: HTTP {status}"
        generation = generated["generation"]
        assert generation["status"] == "succeeded"
        status, repeated = request("POST", body, "/generate")
        assert status == 200 and repeated["generation"] == generation, "Replay must reuse persisted result"
        status, after = request()
        assert status == 200 and after["revision"] == before["revision"] and after["dataset_card"] == before["dataset_card"], "Generation changed approved card"
        result = generation["result"]
        assert result["basis"] == "labels_and_statistics_only"
        print(json.dumps({"generation_id": generation["id"], "model": result["model"], "prompt_version": result["prompt_version"], "latency_ms": result["latency_ms"], "tokens": result["input_tokens"] + result["output_tokens"]}))
    print("dataset card acceptance passed")


if __name__ == "__main__":
    main()
