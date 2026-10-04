"""Only the researcher-owned controller can call providers. No worker URL input."""

import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .event_log import canonical, digest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("provider redirect refused")


def load_env(path: Path) -> None:
    """Read only the explicitly supplied project file; never expand shell expressions."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, sep, value = line.partition("=")
            if not sep or not key.replace("_", "").isalnum():
                raise ValueError("invalid .env entry")
            os.environ.setdefault(key, value.strip().strip("\"'"))


class MalformedResponse(ValueError):
    """The provider answered, and was paid, but the answer is not one complete JSON object.
    The runner records the turn as malformed and moves on; nothing is retried."""

    def __init__(self, message, finish_reason=None):
        super().__init__(message)
        self.finish_reason = finish_reason


class ProviderUnavailable(RuntimeError):
    """Transport failure with safe diagnostics and an explicit circuit-breaker decision."""

    def __init__(self, *, http_status=None, error_kind=None, error_number=None):
        self.details = {"http_status": http_status, "error_kind": error_kind, "error_number": error_number}
        self.fatal = True
        label = (
            f"HTTP {http_status}"
            if http_status is not None
            else f"connection failed ({error_kind}, errno={error_number})"
        )
        super().__init__(f"provider {label}; reservation retained")


class Provider:
    ENDPOINTS = {
        "Jev": "https://api.typesafe.ai/v1/systemone",
        "worker_agents": "https://openrouter.ai/api/v1/chat/completions",
        "reviewer": "https://openrouter.ai/api/v1/chat/completions",
    }

    def __init__(self, ledger, component: str, *, transport=None, reuse_responses=False):
        self.ledger, self.component = ledger, component
        prefix = {"Jev": "JEV", "worker_agents": "WORKER", "reviewer": "REVIEWER"}[component]
        self.backend = "typesafe" if component == "Jev" else os.environ.get(prefix + "_PROVIDER", "openrouter")
        if self.backend not in {"typesafe", "openrouter", "anthropic", "openai"} or (
            component != "Jev" and self.backend == "typesafe"
        ):
            raise ValueError("unsupported provider")
        self.endpoint = {
            "anthropic": "https://api.anthropic.com/v1/messages",
            "openai": "https://api.openai.com/v1/chat/completions",
        }.get(self.backend, self.ENDPOINTS[component])
        key_name = {
            "typesafe": "TYPESAFE_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
        }[self.backend]
        self.key = os.environ.get(key_name, "")
        # Claude Code cloud environments reserve ANTHROPIC_API_KEY for their own authentication
        # and do not pass it to sessions, so the environment stores the Messages API key under
        # BLADEGRAPH_ANTHROPIC_API_KEY; it takes precedence when set (2026-10-02, Run F).
        if self.backend == "anthropic":
            key_name = "BLADEGRAPH_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY"
            self.key = os.environ.get("BLADEGRAPH_ANTHROPIC_API_KEY", "") or self.key
        self.proxy_auth = os.environ.get(prefix + "_PROXY_AUTH") == "1"
        if self.proxy_auth and self.backend == "anthropic":
            raise ValueError("Claude cloud credential proxy does not attach Anthropic API keys")
        self.model = os.environ.get(prefix + "_MODEL", "")
        if (not self.key and not self.proxy_auth) or not self.model or "latest" in self.model:
            raise ValueError(f"set {key_name} and a pinned {prefix}_MODEL in .env")
        try:
            self.input_price = float(os.environ[prefix + "_INPUT_USD_PER_MILLION"])
            self.output_price = float(os.environ[prefix + "_OUTPUT_USD_PER_MILLION"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"explicit current {prefix} token prices are required") from exc
        if any(not math.isfinite(p) or p < 0 for p in (self.input_price, self.output_price)):
            raise ValueError("invalid token pricing")
        # Request options (2026-10-02, for reasoning models such as GLM 5.3): output cap, reasoning
        # effort, explicit provider routing and provider-side usage accounting. Every option is
        # explicit configuration, recorded in the frozen config and reproduced by the audit.
        self.max_output_tokens = int(os.environ.get(prefix + "_MAX_OUTPUT_TOKENS", "1200"))
        if not 64 <= self.max_output_tokens <= 32768:
            raise ValueError("output token cap out of range")
        effort = os.environ.get(prefix + "_REASONING_EFFORT", "")
        if effort not in {"", "none", "minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError("unknown reasoning effort")
        self.reasoning_effort = effort or None
        order = [x.strip() for x in os.environ.get(prefix + "_PROVIDER_ORDER", "").split(",") if x.strip()]
        self.provider_order = order
        self.usage_accounting = os.environ.get(prefix + "_USAGE_ACCOUNTING", "0") == "1"
        # The OpenAI backend (2026-10-04, series 7 GPT-6.1 sol) has no provider routing and no
        # billed cost in usage; it settles on the configured list prices.
        if self.backend == "openai" and (self.provider_order or self.usage_accounting):
            raise ValueError("provider order and usage accounting are OpenRouter options")
        self.response_schema = None
        self.transport = transport or self._http
        self.records = []
        self.failure_records = []
        self.consecutive_transport_failures = 0
        self.transport_failure_limit = 3
        self.audit_dir = self.ledger.path.parent / "provider-responses"
        self.audit_dir.mkdir(mode=0o700, exist_ok=True)
        self.reuse_responses = reuse_responses

    def _http(self, body: bytes) -> dict:
        headers = {"Content-Type": "application/json"}
        if not self.proxy_auth:
            headers["Authorization"] = "Bearer " + self.key
        if self.backend == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        request = urllib.request.Request(self.endpoint, data=body, headers=headers)
        opener = urllib.request.build_opener(NoRedirect())
        try:
            with opener.open(request, timeout=60) as response:
                data = response.read(1_048_577)
                if len(data) > 1_048_576:
                    raise ValueError("provider response exceeds cap")
                return json.loads(data)
        except urllib.error.HTTPError as exc:
            # Never print response bodies or headers that may contain sensitive content.
            raise ProviderUnavailable(http_status=exc.code) from None
        except urllib.error.URLError as exc:
            number = getattr(exc.reason, "errno", None)
            raise ProviderUnavailable(
                error_kind=type(exc.reason).__name__, error_number=number if type(number) is int else None
            ) from None
        except OSError as exc:
            raise ProviderUnavailable(error_kind=type(exc).__name__, error_number=exc.errno) from None

    def call(self, payload: dict) -> dict:
        body = canonical({**payload, "model": self.model}).encode()
        request_hash = digest({"endpoint": self.endpoint, "payload": json.loads(body)})
        if self.reuse_responses:
            for path in sorted(self.audit_dir.glob("*.json")):
                saved = json.loads(path.read_text())
                legacy_hash = digest(json.loads(body)) if self.backend in {"typesafe", "openrouter"} else None
                if (
                    saved.get("request_hash") in {request_hash, legacy_hash}
                    and saved.get("request_hash") is not None
                    and "response" in saved
                ):
                    result = saved["response"]
                    usage = result.get("usage", {})
                    row = self.ledger.db.execute("SELECT usd, status FROM costs WHERE id=?", (path.stem,)).fetchone()
                    if row is None:
                        raise ValueError("cached response has no accounting record")
                    self.records.append(
                        {
                            "component": self.component,
                            "model": result.get("model", self.model),
                            "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
                            "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
                            "usd": row[0],
                            "cost_basis": "retained original ledger entry: " + row[1],
                            "audit_id": path.stem,
                            "latency_seconds": None,
                            "cache_replay": True,
                        }
                    )
                    return result
        # Bytes are a conservative input token estimate; Jev may account per question.
        multiplier = len(payload.get("questions", {})) or 1
        input_bound = (len(body) + 2048) * multiplier
        output_bound = (
            8192 if self.component == "Jev" else payload.get("max_tokens", payload.get("max_completion_tokens"))
        )
        reserve = (input_bound * self.input_price + output_bound * self.output_price) / 1e6
        key = self.ledger.reserve(self.component, reserve)
        start = time.monotonic()
        audit_path = self.audit_dir / (key + ".json")
        try:
            result = self.transport(body)
        except ProviderUnavailable as exc:
            self.consecutive_transport_failures += 1
            status = exc.details.get("http_status")
            permanent = status is not None and 400 <= status < 500 and status not in {408, 429}
            exc.fatal = permanent or self.consecutive_transport_failures >= self.transport_failure_limit
            failure = {
                "audit_id": key,
                "component": self.component,
                "reserved_usd": reserve,
                "request_hash": request_hash,
                "consecutive_failures": self.consecutive_transport_failures,
                "fatal": exc.fatal,
                **exc.details,
            }
            with audit_path.open("x") as file:
                file.write(canonical({"request_hash": request_hash, "transport_failure": failure}))
            audit_path.chmod(0o600)
            self.failure_records.append(failure)
            raise
        self.consecutive_transport_failures = 0
        with audit_path.open("x") as file:
            file.write(canonical({"request_hash": request_hash, "response": result}))
        audit_path.chmod(0o600)
        usage = result.get("usage", {})
        n_in = usage.get("input_tokens", usage.get("prompt_tokens"))
        n_out = usage.get("output_tokens", usage.get("completion_tokens"))
        details = usage.get("completion_tokens_details") or {}
        meta = {
            "provider": result.get("provider"),
            "finish_reason": ((result.get("choices") or [{}])[0].get("finish_reason") or result.get("stop_reason")),
            "reasoning_tokens": details.get("reasoning_tokens") if isinstance(details, dict) else None,
        }
        if any(n is not None and (type(n) is not int or n < 0) for n in (n_in, n_out)):
            raise ValueError("invalid usage; reservation retained")
        if n_in is None or n_out is None:
            # TypeSafe permits absent token counts. Preserve the upper bound rather
            # than invent zero usage or discard an otherwise valid semantic response.
            self.records.append(
                {
                    "component": self.component,
                    "model": result.get("model", self.model),
                    "input_tokens": n_in,
                    "output_tokens": n_out,
                    "usd": reserve,
                    "cost_basis": "unsettled conservative reservation",
                    "audit_id": key,
                    "latency_seconds": time.monotonic() - start,
                    **meta,
                }
            )
            return result
        estimated = (n_in * self.input_price + n_out * self.output_price) / 1e6
        actual = usage.get("cost")
        if actual is None:
            actual = estimated
        if isinstance(actual, bool) or not isinstance(actual, (float, int)):
            raise ValueError("invalid billed cost; reservation retained")
        self.ledger.settle(key, actual, n_in + n_out)
        self.records.append(
            {
                "component": self.component,
                "model": result.get("model", self.model),
                "input_tokens": n_in,
                "output_tokens": n_out,
                "usd": actual,
                "cost_basis": "provider" if usage.get("cost") is not None else "configured prices",
                "audit_id": key,
                "latency_seconds": time.monotonic() - start,
                **meta,
            }
        )
        return result

    def request_options(self) -> dict:
        """The OpenRouter chat options beyond messages: identical across a run, recorded in the
        frozen config, and merged the same way by the boundary audit's hash reconstruction."""
        if self.backend == "openai":
            # OpenAI chat completions: the cap includes reasoning tokens; effort is top level.
            options = {"max_completion_tokens": self.max_output_tokens}
            if self.reasoning_effort is not None:
                options["reasoning_effort"] = self.reasoning_effort
            if self.response_schema is not None:
                options["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "behavior_review", "strict": True, "schema": self.response_schema},
                }
            return options
        options = {"max_tokens": self.max_output_tokens}
        if self.reasoning_effort is not None:
            options["reasoning"] = (
                {"exclude": True, "effort": "minimal"}
                if self.reasoning_effort == "none"
                else {"effort": self.reasoning_effort}
            )
        if self.provider_order:
            options["provider"] = {"order": self.provider_order, "allow_fallbacks": False, "require_parameters": True}
        if self.usage_accounting:
            options["usage"] = {"include": True}
        if self.response_schema is not None:
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "behavior_review", "strict": True, "schema": self.response_schema},
            }
        return options

    def chat(self, system: str, state: dict) -> dict:
        payload = {
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": canonical(state)}],
            "max_tokens": 1200,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "plugins": [],
            **self.request_options(),
        }
        if self.backend == "openai":
            # No temperature or plugins: OpenAI reasoning models reject both.
            payload = {
                "messages": payload["messages"],
                "response_format": {"type": "json_object"},
                **self.request_options(),
            }
        if self.backend == "anthropic":
            # No sampling parameters: Claude Opus 5.5 rejects temperature with a 400, and its
            # always-on thinking counts toward max_tokens (2026-10-02, Run F preflight).
            payload = {
                "system": system,
                "messages": [{"role": "user", "content": canonical(state)}],
                "max_tokens": self.max_output_tokens,
            }
        result = self.call(payload)
        if self.backend == "anthropic":
            if result.get("stop_reason") != "end_turn":
                raise MalformedResponse("incomplete Anthropic response", result.get("stop_reason"))
            content = "".join(block["text"] for block in result.get("content", []) if block.get("type") == "text")
            finish = result.get("stop_reason")
        else:
            choice = (result.get("choices") or [{}])[0]
            content = choice.get("message", {}).get("content")
            finish = choice.get("finish_reason")
            if finish == "length":
                raise MalformedResponse("response truncated at the output cap", finish)
        if not isinstance(content, str) or len(content) > 16000:
            raise MalformedResponse("invalid model response", finish)
        content = content.strip()
        if content.startswith("```json\n") and content.endswith("\n```"):
            content = content[8:-4]
        elif content.startswith("```\n") and content.endswith("\n```"):
            content = content[4:-4]
        try:
            value = json.loads(content)
        except ValueError:
            raise MalformedResponse("model response is not valid JSON", finish) from None
        if not isinstance(value, dict):
            raise MalformedResponse("model response must be a JSON object", finish)
        return value
