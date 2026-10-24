"""Shared test doubles."""


class FakeBedrock:
    def __init__(self, estimate=120, lo=80, hi=200, confidence=0.85, recognised=True):
        self.payload = {"estimate": estimate, "min": lo, "max": hi, "confidence": confidence,
                        "recognised": recognised, "rationale": "test"}
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        if "toolConfig" in kwargs:
            return {"output": {"message": {"content": [{"toolUse": {"name": "record_estimate", "input": self.payload}}]}},
                    "usage": {}}
        return {"output": {"message": {"content": [{"text": "A tailored managed services proposal."}]}}}
