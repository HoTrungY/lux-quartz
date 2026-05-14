from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None


ToolHandler = Callable[..., Dict[str, Any]]


class OpenAIOrchestrator:
    def __init__(self, *, api_key: str, model: str, system_prompt: str, temperature: float = 0.2) -> None:
        if OpenAI is None:
            raise RuntimeError("openai package is not installed")
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature

    def chat_with_tools(
        self,
        *,
        user_message: str,
        history: List[dict],
        tools: List[Dict[str, Any]],
        handlers: Dict[str, ToolHandler],
        max_rounds: int = 6,
        system_prompt_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        system_prompt_to_use = system_prompt_override if system_prompt_override is not None else self.system_prompt
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt_to_use},
            *history,
            {"role": "user", "content": user_message},
        ]
        audit: List[Dict[str, Any]] = []

        for _ in range(max(1, max_rounds)):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                temperature=self.temperature,
            )
            message = response.choices[0].message

            tool_calls = message.tool_calls or []
            assistant_payload: Dict[str, Any] = {
                "role": "assistant",
                "content": message.content or "",
            }

            if tool_calls:
                assistant_payload["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in tool_calls
                ]

            messages.append(assistant_payload)

            if not tool_calls:
                return {
                    "reply": (message.content or "").strip(),
                    "audit": audit,
                }

            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                raw_arguments = tool_call.function.arguments or "{}"
                try:
                    args = json.loads(raw_arguments)
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}

                handler = handlers.get(tool_name)
                if not handler:
                    result: Dict[str, Any] = {
                        "ok": False,
                        "error": f"Unknown tool: {tool_name}",
                    }
                else:
                    try:
                        result = handler(**args)
                    except TypeError as exc:
                        result = {
                            "ok": False,
                            "error": f"Invalid arguments for {tool_name}",
                            "detail": str(exc),
                        }
                    except Exception as exc:  # pragma: no cover
                        result = {
                            "ok": False,
                            "error": f"Tool execution failed: {tool_name}",
                            "detail": str(exc),
                        }

                audit.append({"tool": tool_name, "args": args, "result": result})

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        return {
            "reply": "Dạ Anh/Chị, em đang gặp lỗi tạm thời khi xử lý yêu cầu. Anh/Chị cho em xin Zalo/WhatsApp để Lux Quartz hỗ trợ ngay ạ.",
            "audit": audit,
        }
