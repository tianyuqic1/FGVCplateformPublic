# FineVision HTML Prototypes

This is a throwaway prototype for discussing the future fine-grained image classification platform UI.

The current visual direction has moved from a website-style concept page to a deeper workbench prototype: compact SaaS platform shell, left navigation, sticky top context, clickable subpages, hover transitions, page transitions, progress shimmer, pipeline pulse animation, and toast feedback. It keeps some polished infrastructure feel without behaving like a marketing homepage.

Open:

```text
frontend/prototypes/fine-grained-vision-platform.html
```

Main pages:

- `工作台`: operational overview and priority tasks.
- `数据集`: dataset list and dataset detail tabs.
- `训练`: training queue and run detail.
- `推理实验室`: single-image inference with abstention and nearest-neighbor explanation.
- `人工复核`: review queue and review detail form.
- `模型版本`: model registry and release gate detail.
- `流水线`: workflow template and run detail.

Question being answered:

Which product shape best fits the MVP before writing a formal product/design document?

## LLM-led review entry prototype

Question being answered:

Where should an optional LLM-led, no-manual-touch review mode live without weakening the existing human-review workflow or implying that its backend is already available?

Preview the existing `/review` route with:

- `?variant=A`: a prominent review-mode command bar above the queue.
- `?variant=B`: manual and LLM modes as tabs inside the queue.
- `?variant=C`: an automation task entry in the right-hand operational rail.

All automation actions are intentionally disabled. After one placement is selected, remove the other variants and replace the selected prototype with tested production code.

Decision: variant C was selected. The right-hand automation task entry is now the retained placeholder on `/review`; variants A/B and the prototype switcher were removed.
