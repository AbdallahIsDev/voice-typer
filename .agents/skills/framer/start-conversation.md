# Prompt The Framer Agent

Read this file only when the user explicitly asks to prompt the Framer agent, use `startConversation`, or delegate a design task to Framer's agent.

Use `framer.agent.startConversation()` to start a stateful design subagent. Keep the `conversationId` it returns in `state`, and call `framer.agent.continueConversation()` with it to continue the same design task.

Do not call `framer.agent.getSystemPrompt()`, `framer.agent.getContext()`, or `framer.agent.applyChanges()` with this approach.

```js
state.agent ??= {};

const first = await framer.agent.startConversation(
	"Build me a landing page based on the attached screenshot",
	{
		pagePath: "/",
		imageUrls: ["https://example.com/image.png"],
		// selectionNodeIds: [...]
	},
);

state.agent.conversationId = first.conversationId;
console.log(first.responseMessages);

const second = await framer.agent.continueConversation("Now make it pink", {
	conversationId: state.agent.conversationId,
	selectionNodeIds: ["someNodeId"],
	// imageUrls: [...]
	// changing pagePath or model is not supported
});
console.log(second.responseMessages);
```

Prompting may take a while to complete, so set the command timeout to 10 minutes.
