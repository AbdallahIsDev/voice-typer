# Static Recipes

These are patterns only. Before using any method below, run `docs <method-or-class>` to verify the current signature.

## CMS Items

Collections and items are nodes. Read them with agent project methods, and create or edit them with `framer.agent.applyChanges` where possible. A collection's fields are its `variables`; an item's cells are `$control__<fieldId>` attributes.

List collections and fields:

```js
const collections = await framer.agent.getNodesOfTypes({ types: ["CollectionNode"] });
console.log(collections.map((collection) => ({
	id: collection.id,
	name: collection.name,
	itemCount: collection.$itemCount,
	fields: collection.variables.map((field) => ({
		id: field.id,
		name: field.name,
		type: field.type,
	})),
})));
```

Read direct collection items:

```js
const collection = await framer.agent.serialize({
	id: "collection-id",
	depth: 1,
});
console.log(collection.children ?? []);
```

Create or edit items with `applyChanges`:

```js
console.log(await framer.agent.applyChanges(
	`+CollectionItemNode newPost parent="collection-id";
	 SET newPost $control__slug="hello-world" $control__title="Hello World";`,
));
```

## Images

Canvas editing accepts image URLs directly when setting an image fill.

Source stock images:

```js
const { results } = await framer.agent.queryImages({
	source: "unsplash",
	query: "snow-capped mountains",
	count: 4,
	orientation: "landscape",
});
state.heroUrl = results[0].url;
```

Upload an external image if it will be reused:

```js
state.heroUrl = (await framer.uploadImage({
	image: "https://example.com/hero.png",
	altText: "Mountain range at sunset",
})).url;
```

Reuse an existing canvas image URL:

```js
const node = await framer.agent.getNode({ id: "image-node-id" });
state.heroUrl = node.attributes.fill;
```

Add inline SVGs through the plugin API:

```js
await framer.addSVG({
	svg: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><circle cx="10" cy="10" r="8"/></svg>',
	name: "circle.svg",
});
```

## Plugin Data

Store metadata on nodes or globally in the project.

```js
await framer.setPluginData("myKey", "myValue");
const value = await framer.getPluginData("myKey");

await node.setPluginData("processed", "true");
const nodeData = await node.getPluginData("processed");
```

Set a key to `null` to delete it.

## Localization

```js
const locales = await framer.getLocales();
const groups = await framer.getLocalizationGroups();
const french = locales.find((locale) => locale.code === "fr");

await framer.setLocalizationData({
	valuesBySource: {
		[sourceId]: {
			[french.id]: { action: "set", value: "Bonjour" },
		},
	},
});
```

## Known Limitations

- Pages: cannot change the path of a page.
- Code overrides: cannot assign overrides to nodes.
