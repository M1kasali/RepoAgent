"""Issue-only graceful closeout; host budgets and acceptance stay authoritative."""

from dataclasses import replace

from ..providers.base import ModelMessage


def validate_closeout_result(request, result, *, remaining):
    if request.call_kind == "agent" and remaining == 1 and result.tool_calls:
        raise RuntimeError("Issue report-only call returned a tool invocation")
    return result


def budgeted_request(request, *, remaining):
    if request.call_kind != "agent" or remaining > 6:
        return request
    if remaining < 1:
        raise ValueError("no model calls remain")
    if remaining == 1:
        instruction = (
            "Issue workflow closeout: this is the final model call. No tools are available. "
            "Report the current changes, checks actually executed, their observed outcomes "
            "and any unfinished work or uncertainty. Do not claim an unexecuted check passed, "
            "invent results or claim the issue is fixed. This report is not proof of success; "
            "the independent host verifier decides whether a patch satisfies acceptance."
        )
    else:
        instruction = (
            f"Issue workflow budget: {remaining} model calls remain including this one; "
            "the final call is reserved for reporting without tools. "
            "Do not expand investigation or add a new test matrix. Finish the smallest "
            "existing reproduction/regression check relevant to the current change. "
            "If a check is broken, correct and rerun it before claiming a pass. "
            "If the required checks already passed, report now; otherwise explain "
            "what remains unfinished. Do not alter tests merely to hide a failure."
        )
    return replace(
        request,
        messages=request.messages + (ModelMessage(role="system", content=instruction),),
        tools=() if remaining == 1 else request.tools,
    )
