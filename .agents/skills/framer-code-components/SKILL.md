---
name: framer-code-components
description: "Framer code component implementation guidance, platform constraints, layout annotations, property controls, and authoring best practices. Use only after loading the framer skill and reading the Components row of the generated projects/<safeProjectId>/index.md task map first in the same task; never load this skill directly as the entry point."
---

# Framer Code Components

## Best Practices

### Component Structure

```tsx
import { addPropertyControls, ControlType } from "framer";
import { motion } from "framer-motion"; // NOT from "framer"

interface MyComponentProps {
  /* typed props */
}

/**
 * @framerSupportedLayoutWidth any-prefer-fixed
 * @framerSupportedLayoutHeight any-prefer-fixed
 */
export default function MyComponent(props: MyComponentProps) {
  // component
}

addPropertyControls(MyComponent, {
  /* controls */
});
```

### Platform Constraints

These will cause errors if violated:

1. **Single file, default export** - Use named `function` syntax (not arrow functions), no named exports
2. **Imports** - Only `react`, `react-dom`, `framer`, `framer-motion`. Import `motion` from `"framer-motion"`, not `"framer"`
3. **Position** - Use `position: relative` on the root element, never `fixed`
4. **SSR** - Guard `window`/`document` access: `if (typeof window !== "undefined")`
5. **Annotations** - Include `@framerSupportedLayoutWidth/Height` in a `/** */` block comment immediately above the component function
6. **Types** - Provide a typed props interface (e.g. `MyComponentProps`). Avoid NodeJS types like `Timeout` — use `number` instead

### Layout Annotations

| Content           | Width              | Height             |
| ----------------- | ------------------ | ------------------ |
| No intrinsic size | `fixed`            | `fixed`            |
| Text/auto-sizing  | `auto`             | `auto`             |
| Flexible          | `any-prefer-fixed` | `any-prefer-fixed` |

Detect auto vs fixed sizing: check if `style.width` or `style.height` is `"100%"`.

### Property Controls

To make components configurable in Framer's properties panel, add property controls:

- To make colors customizable, use `ControlType.Color`. Reuse the same prop for elements sharing a color.
- To make text styling customizable, use `ControlType.Font` with `controls: "extended"` and `defaultFontType: "sans-serif"`.
- For images, use `ControlType.ResponsiveImage`. Set defaults in the component body via destructuring (the control doesn't support `defaultValue`).
- Provide a `defaultValue` for every prop so components render correctly in the Framer canvas. Include at least one item in `ControlType.Array` controls.
- `ComponentName.defaultProps` is not supported in Framer — use `defaultValue` on the property control instead.
- Use `hidden` for conditional visibility: `hidden: (props) => !props.showFeature`
- Prefer sliders over steppers unless step values are large.
- Keep controls focused — make key elements configurable, hardcode the rest.
- Full property control reference is included below in this same skill.

### Image Defaults (in component body)

```tsx
const {
  image = {
    src: "https://framerusercontent.com/images/GfGkADagM4KEibNcIiRUWlfrR0.jpg",
    alt: "Default",
  },
} = props;
```

### Animation Performance

```tsx
import { useIsStaticRenderer } from "framer";
import { useInView } from "framer-motion";

const isStatic = useIsStaticRenderer();
const ref = useRef(null);
const isInView = useInView(ref);

if (isStatic) return <StaticPreview />; // Show useful static state
// Pause animations when out of viewport
```

- For very complex animations, consider WebGL instead of `framer-motion`.
- Static preview should include visual effects, not just text.
- Wrapping state updates in `startTransition()` prevents UI blocking and keeps interactions smooth.

### Text

- For auto-sized components with text, apply `width: max-content` or `minWidth: max-content` to prevent text from collapsing.

### Common Errors

- WebGL cross-origin: handle `SecurityError: Failed to execute 'texImage2D'` for cross-origin images.
- Inverted Y-axis: check if WebGL images render upside down and accommodate.

### Accessibility

- `aria` roles on interactive elements
- Semantic HTML (`<nav>`, `<article>`, `<section>`)
- `alt=""` on decorative images
- 4.5:1 color contrast

## Term Interpretation

- "responsive" → width/height 100%
- "modern" → 8px radius, 16px spacing, subtle shadows
- "minimal" → limited colors, whitespace
- "interactive" → hover/active states
- "accessible" → ARIA, semantic HTML
- "props"/"properties" → Framer property controls

# Framer Property Controls

Control types for property controls in Framer code components. Each control type specifies a different user interface for receiving input. All control types accept `title`, `description`, and `hidden` properties.

## Table of Contents

- [Boolean](#boolean)
- [Number](#number)
- [String](#string)
- [Enum](#enum)
- [Color](#color)
- [ResponsiveImage](#responsiveimage)
- [File](#file)
- [Array](#array)
- [Slot](#slot)
- [EventHandler](#eventhandler)
- [Font](#font)
- [Transition](#transition)
- [BoxShadow](#boxshadow)
- [Link](#link)
- [Date](#date)
- [Object](#object)
- [Border](#border)
- [Cursor](#cursor)
- [Padding](#padding)
- [BorderRadius](#borderradius)
- [Gap](#gap)
- [TrackingId](#trackingid)
- [Deprecated Controls](#deprecated-controls)
- [TypeScript Interfaces](#typescript-interfaces)

## Boolean

A control that displays an on/off checkbox. The associated property will be `true` or `false`, depending on the state of the checkbox.

**Properties:**

- `defaultValue`: Set to `true` by default
- `optional`: Whether the control value is optional
- `enabledTitle`: Customize the label when enabled _(deprecated)_
- `disabledTitle`: Customize the label when disabled _(deprecated)_

**Example:**

```javascript
export function MyComponent(props) {
  return <Frame size={"100%"}>{props.showText ? "Hello World" : null}</Frame>;
}

addPropertyControls(MyComponent, {
  showText: {
    type: ControlType.Boolean,
    title: "Show Text",
    defaultValue: true,
  },
});
```

## Number

A control that accepts any numeric value. This will be provided directly as a property. Displays an input field with a range slider by default.

**Properties:**

- `defaultValue`: The default numeric value
- `min`: Minimum allowed value
- `max`: Maximum allowed value
- `unit`: Unit label (e.g., "deg", "px")
- `step`: Increment value for the slider
- `displayStepper`: Enable to show a stepper control instead
- `optional`: Whether the control value is optional

**Example:**

```javascript
export function MyComponent(props) {
  return (
    <Frame rotateZ={props.rotation} size={"100%"}>
      {props.rotation}
    </Frame>
  );
}

addPropertyControls(MyComponent, {
  rotation: {
    type: ControlType.Number,
    defaultValue: 0,
    min: 0,
    max: 360,
    unit: "deg",
    step: 0.1,
    displayStepper: true,
  },
});
```

## String

A control that accepts plain text values. Displays an input field with an optional placeholder value.

**Properties:**

- `defaultValue`: The default text value
- `placeholder`: Placeholder text
- `obscured`: Set to true to use a password input field
- `displayTextArea`: Enable for a multi-line input area
- `preventLocalization`: Prevents automatic translation of the text
- `optional`: Whether the control value is optional

**Example:**

```javascript
export function MyComponent(props) {
  return (
    <Frame>
      {props.title} — {props.body}
    </Frame>
  );
}

addPropertyControls(MyComponent, {
  title: {
    type: ControlType.String,
    defaultValue: "Framer",
    placeholder: "Type something…",
  },
  body: {
    type: ControlType.String,
    defaultValue: "Lorem ipsum dolor sit amet.",
    placeholder: "Type something…",
    displayTextArea: true,
  },
});
```

## Enum

A property control that represents a list of options. The selected option will be provided as a property. Displayed as a dropdown menu.

**Properties:**

- `defaultValue`: The default selected option (string, boolean, number, or null)
- `options`: Array of unique values (string, boolean, number, or null)
- `optionTitles`: Display names for the options
- `displaySegmentedControl`: Enable to display a segmented control instead
- `segmentedControlDirection`: Direction of the segmented control ('horizontal' or 'vertical')

**Example:**

```javascript
export function MyComponent(props) {
  const value = props.value || "a";
  const colors = { a: "red", b: "green", c: "blue" };
  return (
    <Frame background={colors[value]} size={"100%"}>
      {value}
    </Frame>
  );
}

addPropertyControls(MyComponent, {
  value: {
    type: ControlType.Enum,
    defaultValue: "a",
    options: ["a", "b", "c"],
    optionTitles: ["Option A", "Option B", "Option C"],
  },
});
```

**Example with Segmented Control:**

```javascript
addPropertyControls(MyComponent, {
  alignment: {
    type: ControlType.Enum,
    defaultValue: "left",
    options: ["left", "center", "right"],
    optionTitles: ["Left", "Center", "Right"],
    displaySegmentedControl: true,
    segmentedControlDirection: "horizontal",
  },
});
```

## Color

A control that represents a color value. The selected color is provided as a string in either HEX (`"#fff"`) or HSL (`hsla(203, 87%, 50%, 0.5)`) notation, depending on whether there is an alpha channel.

**Properties:**

- `defaultValue`: The default color value
- `optional`: Whether the color is optional

**Example:**

```javascript
function MyComponent(props) {
  return <Frame background={props.background} size={"100%"} />;
}

addPropertyControls(MyComponent, {
  background: {
    type: ControlType.Color,
    defaultValue: "#fff",
  },
});
```

## ResponsiveImage

A control that allows the user to pick an image resource. Displayed as an image picker with associated file picker.

**Returns an object with:**

- `src`: URL string of the full resolution image
- `srcSet`: Optional string with scaled image variants (for `<img srcSet>`)
- `alt`: Optional description of the image

**Example:**

```javascript
function MyComponent(props) {
  return (
    <img
      src={props.image?.src}
      srcSet={props.image?.srcSet}
      alt={props.image?.alt}
    />
  );
}

addPropertyControls(MyComponent, {
  image: {
    type: ControlType.ResponsiveImage,
  },
});
```

## File

A control that allows the user to pick a file resource. The selected file will be provided as a fully qualified URL.

**Properties:**

- `allowedFileTypes`: Array specifying acceptable file types. Supported formats:
  - Media types (`"image/png"`, `"audio/*"`, `"*/*"`)
  - File extensions with dot (`".png"`, `".mov"`)
  - File extensions without dot (`"png"`) for backward compatibility
  - Wildcard (`"*"`) to allow everything

**Example:**

```javascript
export function MyComponent(props) {
  return (
    <Frame size={"100%"}>
      <video
        style={{
          objectFit: "contain",
          width: props.width,
          height: props.height,
        }}
        src={props.filepath}
        controls
      />
    </Frame>
  );
}

addPropertyControls(MyComponent, {
  filepath: {
    type: ControlType.File,
    allowedFileTypes: ["mov", "mp4"],
  },
});
```

## Array

A control that allows multiple values per `ControlType`, provided as an array via properties. Displays as an additional section in the properties panel.

**Properties:**

- `control`: The control type to repeat
- `minCount`: Minimum number of items
- `maxCount`: Maximum number of items
- `defaultValue`: Default array values

**Example with Objects:**

```javascript
export function MyComponent(props) {
  return (
    <Stack size={"100%"}>
      {props.items.map((item, index) => (
        <div key={index}>{item.title}</div>
      ))}
    </Stack>
  );
}

addPropertyControls(MyComponent, {
  items: {
    type: ControlType.Array,
    control: {
      type: ControlType.Object,
      controls: {
        title: { type: ControlType.String, defaultValue: "Item" },
        image: { type: ControlType.ResponsiveImage },
      },
    },
    defaultValue: [{ title: "First" }, { title: "Second" }],
    maxCount: 10,
  },
});
```

## Slot

A control that references one or more other components on the canvas, included in the component props as a React node. By default allows any number of components to be linked.

**Properties:**

- `maxCount`: Maximum number of components to be linked

**Example:**

```javascript
export function MyComponent(props) {
  return <Stack size={"100%"}>{props.children}</Stack>;
}

addPropertyControls(MyComponent, {
  children: {
    type: ControlType.Slot,
    maxCount: 5,
  },
});
```

## EventHandler

A control that exposes events in the prototyping panel within the Framer UI. When choosing an event, you can select from a list of actions to trigger.

**Example:**

```javascript
export function MyComponent(props) {
  return <Frame onTap={props.onTap} size={"100%"} />;
}

addPropertyControls(MyComponent, {
  onTap: {
    type: ControlType.EventHandler,
  },
});
```

## Font

A control that allows for selecting a font to be used in the component.

**Properties:**

- `defaultValue`: Default font settings
- `controls`: Specifies control options ("basic" or "extended")
- `defaultFontType`: Default font type ("sans-serif", "serif", or "monospace")
- `displayTextAlignment`: Whether to display text alignment options
- `displayFontSize`: Whether to display font size options

**Default Value Options:**

- `textAlign`: "left", "right", "center", or "justify"
- `fontSize`: string or number (e.g., "16px", 16)
- `letterSpacing`: string or number (e.g., "-0.01em", 0.1)
- `lineHeight`: string or number (e.g., "1.5em", 1.5, "150%")
- `variant`: Font variant (only for "sans-serif" font type)

**Example:**

```javascript
export function MyComponent(props) {
  return <div style={props.customFont}>Hello World</div>;
}

addPropertyControls(MyComponent, {
  customFont: {
    type: ControlType.Font,
    defaultValue: {
      fontSize: "16px",
      variant: "Medium",
      letterSpacing: "-0.01em",
      lineHeight: "1.2em",
      textAlign: "left",
    },
    controls: "extended",
    defaultFontType: "sans-serif",
  },
});
```

## Transition

A control that allows for editing Framer Motion transition options within the Framer UI.

**Properties:**

- `defaultValue`: Default transition (null or Transition object)

**Example:**

```javascript
export function MyComponent(props) {
  return <Frame animate={{ scale: 2 }} transition={props.transition} />;
}

addPropertyControls(MyComponent, {
  transition: {
    type: ControlType.Transition,
  },
});
```

## BoxShadow

A control that allows for exposing shadows. The value will be provided as a string with valid CSS box-shadow values.

**Properties:**

- `defaultValue`: Default shadow (string or BoxShadow array)

**Example:**

```javascript
export function MyComponent(props) {
  return <motion.div style={{ boxShadow: props.shadow }} />;
}

addPropertyControls(MyComponent, {
  shadow: {
    type: ControlType.BoxShadow,
  },
});
```

## Link

A control that allows for exposing web links.

**Properties:**

- `defaultValue`: Default URL as string

**Example:**

```javascript
export function MyComponent(props) {
  return <a href={props.link}>My Link</a>;
}

addPropertyControls(MyComponent, {
  link: {
    type: ControlType.Link,
  },
});
```

## Date

A control that allows for exposing dates. The value will be provided in toJSON() string format.

**Properties:**

- `displayTime`: Whether to include time selection
- `defaultValue`: Default date as ISO string
- `optional`: Whether the date is optional

**Example:**

```javascript
export function MyComponent(props) {
  const formattedDate = React.useMemo(() => {
    return props.date ? new Date(props.date).toLocaleDateString() : "No date";
  }, [props.date]);
  return <div>{formattedDate}</div>;
}

addPropertyControls(MyComponent, {
  date: {
    type: ControlType.Date,
    displayTime: true,
    optional: true,
  },
});
```

## Object

A control that allows for grouping multiple properties as an object.

**Properties:**

- `controls`: Object containing nested controls
- `defaultValue`: Default object values
- `buttonTitle`: Custom button title
- `optional`: Whether the object is optional
- `icon`: Icon to display ('object', 'effect', 'color', 'interaction', or 'boolean')

**Example:**

```javascript
export function MyComponent(props) {
  return (
    <Frame opacity={props.style?.opacity} background={props.style?.tint} />
  );
}

addPropertyControls(MyComponent, {
  style: {
    type: ControlType.Object,
    optional: true,
    icon: "effect",
    controls: {
      opacity: { type: ControlType.Number, defaultValue: 1, min: 0, max: 1 },
      tint: { type: ControlType.Color, defaultValue: "#000" },
    },
  },
});
```

## Border

A control that represents a border.

**Properties:**

- `defaultValue`: Default border settings
- `optional`: Whether the border is optional

**Border Value Object:**

- `borderColor`: CSS color string
- `borderStyle`: "solid", "dashed", "dotted", or "double"
- `borderWidth`: Uniform width (number)
- `borderTopWidth`, `borderLeftWidth`, `borderRightWidth`, `borderBottomWidth`: Per-side widths

**Example:**

```javascript
function MyComponent(props) {
  return <div style={props.border} />;
}

addPropertyControls(MyComponent, {
  border: {
    type: ControlType.Border,
    defaultValue: {
      borderWidth: 1,
      borderStyle: "solid",
      borderColor: "rgba(0, 0, 0, 0.5)",
    },
  },
});
```

## Cursor

A control that allows specifying a web cursor that should be shown when mousing over the element assigned.

**Properties:**

- `defaultValue`: Default cursor value (CSS cursor string)

**Example:**

```javascript
function MyComponent(props) {
  return <div style={{ cursor: props.cursor }}>Hover me</div>;
}

addPropertyControls(MyComponent, {
  cursor: {
    type: ControlType.Cursor,
    defaultValue: "pointer",
  },
});
```

## Padding

A control that represents CSS padding.

**Properties:**

- `defaultValue`: Default padding value (e.g., "8px", "10px 20px", "10px 20px 30px 40px")

**Example:**

```javascript
function MyComponent({ padding }) {
  return <div style={{ padding }}>Content</div>;
}

addPropertyControls(MyComponent, {
  padding: {
    type: ControlType.Padding,
    defaultValue: "8px",
  },
});
```

## BorderRadius

A control that represents CSS border radius.

**Properties:**

- `defaultValue`: Default border radius value (e.g., "16px", "8px 16px")

**Example:**

```javascript
function MyComponent({ borderRadius }) {
  return <div style={{ borderRadius, background: "red" }} />;
}

addPropertyControls(MyComponent, {
  borderRadius: {
    type: ControlType.BorderRadius,
    defaultValue: "16px",
    title: "Radius",
  },
});
```

## Gap

A control that represents CSS gap for grid/flex layouts.

**Properties:**

- `defaultValue`: Default gap value (e.g., "8px", "8px 16px")

**Example:**

```javascript
function MyComponent({ gap, children }) {
  return <div style={{ display: "flex", gap }}>{children}</div>;
}

addPropertyControls(MyComponent, {
  gap: {
    type: ControlType.Gap,
    defaultValue: "8px",
  },
  children: {
    type: ControlType.Slot,
  },
});
```

## TrackingId

A control that represents an ID for tracking events.

**Format Requirements:**

- Lowercase letters (a-z) and numbers (0-9) only
- Hyphens (-) as separators (no leading/trailing or consecutive hyphens)
- Valid: "button-click", "form-submit", "video-play", "nav-item-1"
- Invalid: "Button-Click", "form--submit", "-button-click", "button_utils"

**Properties:**

- `defaultValue`: Default tracking ID string

**Example:**

```javascript
function MyComponent(props) {
  const handleClick = () => {
    // Track the event using props.trackingId
    analytics.track(props.trackingId);
  };
  return <button onClick={handleClick}>Click me</button>;
}

addPropertyControls(MyComponent, {
  trackingId: {
    type: ControlType.TrackingId,
    defaultValue: "button-click",
  },
});
```

## Deprecated Controls

### ControlType.Image

**Deprecated.** Use `ControlType.ResponsiveImage` instead. The `src` field provides the image URL.

### ControlType.ComponentInstance

**Deprecated.** Use `ControlType.Slot` instead. The new Slot type doesn't need to be nested within an array control for multiple items. By default, Slot allows infinite items; use `maxCount` to limit.

### ControlType.SegmentedEnum

**Deprecated.** Use `ControlType.Enum` with `displaySegmentedControl: true` instead.

### ControlType.FusedNumber

**Deprecated.** Use `ControlType.Padding` and `ControlType.BorderRadius` instead. These new controls provide a single value (e.g., "10px" or "10px 20px 30px 40px").

---

# Framer Property Control Types

TypeScript interfaces for all Framer property control types. ## Base Control Description

All control descriptions extend this base interface:

```typescript
export interface BaseControlDescription<P = any> {
  title?: string;
  description?: string;
  hidden?: ((props: P, rootProps: any) => boolean) | boolean;
}

export interface WithOptional {
  optional?: boolean;
}
```

## Control Type Interfaces

### Boolean Control

```typescript
export interface BooleanControlDescription<P = any>
  extends BaseControlDescription<P>, WithOptional {
  type: ControlType.Boolean;
  defaultValue?: boolean;
  /** @deprecated */
  disabledTitle?: string;
  /** @deprecated */
  enabledTitle?: string;
}
```

### Number Control

```typescript
export interface NumberControlDescription<P = any>
  extends BaseControlDescription<P>, WithOptional {
  type: ControlType.Number;
  defaultValue?: number;
  max?: number;
  min?: number;
  unit?: string;
  step?: number;
  displayStepper?: boolean;
}
```

### String Control

```typescript
export interface StringControlDescription<P = any>
  extends BaseControlDescription<P>, WithOptional {
  type: ControlType.String;
  defaultValue?: string;
  placeholder?: string;
  obscured?: boolean;
  displayTextArea?: boolean;
  preventLocalization?: boolean;
}
```

### Enum Control

```typescript
export interface EnumControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.Enum;
  defaultValue?: string | boolean | number | null;
  options: (string | boolean | number | null)[];
  optionTitles?: string[];
  displaySegmentedControl?: boolean;
  segmentedControlDirection?: "horizontal" | "vertical";
}
```

### Color Control

```typescript
export interface ColorControlDescription<P = any>
  extends BaseControlDescription<P>, WithOptional {
  type: ControlType.Color;
  defaultValue?: string;
}
```

### ResponsiveImage Control

```typescript
export interface ResponsiveImageControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.ResponsiveImage;
}
```

### File Control

```typescript
export type AllowedFileTypes = readonly string[];

export interface FileControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.File;
  allowedFileTypes: AllowedFileTypes;
}
```

### Slot Control

```typescript
export interface SlotControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.Slot;
  maxCount?: number;
}
```

### Array Control

```typescript
export interface ArrayControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.Array;
  control: ArrayItemControlDescription<P>;
  minCount?: number;
  maxCount?: number;
  defaultValue?: any[];
}
```

### Object Control

```typescript
export type ObjectControlIcon =
  | "object"
  | "effect"
  | "color"
  | "interaction"
  | "boolean";

export interface ObjectControlDescription<P = any>
  extends BaseControlDescription<P>, WithOptional {
  type: ControlType.Object;
  controls: { [key: string]: ObjectPropertyControlDescription };
  defaultValue?: { [key: string]: any };
  buttonTitle?: string;
  icon?: ObjectControlIcon;
}
```

### Event Handler Control

```typescript
export interface EventHandlerControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.EventHandler;
}
```

### Transition Control

```typescript
import type { Transition } from "framer-motion";

export interface TransitionControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.Transition;
  defaultValue?: null | Transition;
}
```

### BoxShadow Control

```typescript
export interface BoxShadowControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.BoxShadow;
  defaultValue?: string | readonly BoxShadow[];
}
```

### Link Control

```typescript
export interface LinkControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.Link;
  defaultValue?: string;
}
```

### Date Control

```typescript
export interface DateControlDescription<P = any>
  extends BaseControlDescription<P>, WithOptional {
  type: ControlType.Date;
  displayTime?: boolean;
  defaultValue?: string;
}
```

### Border Control

```typescript
export type BorderStyle = "solid" | "dashed" | "dotted" | "double";

export interface Border {
  borderColor?: string;
  borderStyle?: BorderStyle;
  borderWidth?: number;
  borderTopWidth?: number;
  borderLeftWidth?: number;
  borderRightWidth?: number;
  borderBottomWidth?: number;
}

export interface BorderControlDescription<P = any>
  extends BaseControlDescription<P>, WithOptional {
  type: ControlType.Border;
  defaultValue?: Border;
}
```

### Cursor Control

```typescript
export interface CursorControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.Cursor;
  defaultValue?: string;
}
```

### Padding Control

```typescript
export interface PaddingControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.Padding;
  defaultValue?: string;
}
```

### Border Radius Control

```typescript
export interface BorderRadiusControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.BorderRadius;
  defaultValue?: string;
}
```

### Gap Control

```typescript
export interface GapControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.Gap;
  defaultValue?: string;
}
```

### Tracking ID Control

```typescript
export interface TrackingIdControlDescription<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.TrackingId;
  defaultValue?: string;
}
```

### Font Control

```typescript
interface FontControlDescriptionBase<
  P = any,
> extends BaseControlDescription<P> {
  type: ControlType.Font;
  controls?: "basic" | "extended";
  displayTextAlignment?: boolean;
  displayFontSize?: boolean;
  defaultValue?: FontControlDefaultValueBase;
}

interface FontControlDescriptionSansSerif<
  P = any,
> extends FontControlDescriptionBase<P> {
  defaultFontType?: "sans-serif";
  defaultValue?: FontControlDefaultValueWithVariant;
}

interface FontControlDescriptionSerif<
  P = any,
> extends FontControlDescriptionBase<P> {
  defaultFontType?: "serif";
  defaultValue?: FontControlDefaultValueBase;
}

interface FontControlDescriptionMonospace<
  P = any,
> extends FontControlDescriptionBase<P> {
  defaultFontType?: "monospace";
  defaultValue?: FontControlDefaultValueBase;
}

export type FontControlDescription<P = any> =
  | FontControlDescriptionSansSerif<P>
  | FontControlDescriptionSerif<P>
  | FontControlDescriptionMonospace<P>;

interface FontControlDefaultValueBase {
  textAlign?: "left" | "right" | "center" | "justify";
  fontSize?: string | number;
  letterSpacing?: string | number;
  lineHeight?: string | number;
}

interface FontControlDefaultValueWithVariant extends FontControlDefaultValueBase {
  variant?: FramerFontVariant;
}

export const framerFontVariants = [
  "Regular",
  "Thin",
  "Extra Light",
  "Light",
  "Medium",
  "Semibold",
  "Bold",
  "Extra Bold",
  "Black",
  "Thin Italic",
  "Extra Light Italic",
  "Light Italic",
  "Italic",
  "Medium Italic",
  "Semibold Italic",
  "Bold Italic",
  "Extra Bold Italic",
  "Black Italic",
  "Regular Italic",
  "Variable",
  "Variable Italic",
] as const;

export type FramerFontVariant = (typeof framerFontVariants)[number];
```

## Composite Types

### All Control Types

```typescript
export type ControlDescription<P = any> =
  | NumberControlDescription<P>
  | EnumControlDescription<P>
  | BooleanControlDescription<P>
  | StringControlDescription<P>
  | ColorControlDescription<P>
  | ResponsiveImageControlDescription<P>
  | FileControlDescription<P>
  | SlotControlDescription<P>
  | ArrayControlDescription<P>
  | EventHandlerControlDescription<P>
  | TransitionControlDescription<P>
  | BoxShadowControlDescription<P>
  | LinkControlDescription<P>
  | DateControlDescription<P>
  | ObjectControlDescription<P>
  | FontControlDescription<P>
  | BorderControlDescription<P>
  | CursorControlDescription<P>
  | PaddingControlDescription<P>
  | BorderRadiusControlDescription<P>
  | GapControlDescription<P>
  | TrackingIdControlDescription<P>;
```

### Property Controls

```typescript
export type PropertyControls<ComponentProps = any, ArrayTypes = any> = {
  [K in keyof ComponentProps]?: ControlDescription<Partial<ComponentProps>>;
};
```

## Associated Methods and Types

### addPropertyControls

```typescript
export declare function addPropertyControls<Props = any>(
  component:
    | React.ComponentType<Props>
    | React.ForwardRefExoticComponent<Props>,
  propertyControls: PropertyControls<Props>,
): void;
```

### addFonts

```typescript
export declare function addFonts(
  component: React.ComponentType<unknown>,
  fonts: any[],
  flags?: { supportsExplicitInterCodegen?: boolean },
): void;
```

### Data API

```typescript
export declare const Data: {
  <T extends object = object>(initial?: Partial<T> | object): T;
};
```

### Renderer Detection APIs

```typescript
export declare type RenderTarget = RenderTargetName;

export declare const RenderTarget: {
  canvas: RenderTargetName;
  export: RenderTargetName;
  thumbnail: RenderTargetName;
  preview: RenderTargetName;
  current: () => RenderTargetName;
  hasRestrictions: () => boolean;
};

/** Check if executed in a Framer Canvas or Export Canvas environment */
export declare function isStaticRenderer(): boolean;

/** Hook to check if in a static renderer (Canvas or Export) */
export declare function useIsStaticRenderer(): boolean;

/** Hook to observe data changes */
export declare function useObserveData(): boolean;
```

### Color Interface and Utilities

```typescript
export interface Color {
  r: number;
  g: number;
  b: number;
  h: number;
  s: number;
  l: number;
  a: number;
  roundA: number;
  format: ColorFormat;
  initialValue?: string;
  isValid?: boolean;
  mix: any;
  toValue: () => string;
}

export enum ColorFormat {
  RGB = "rgb",
  HSL = "hsl",
  HSV = "hsv",
  HEX = "hex",
  NAME = "name",
}
```

---

# Property Control Guide

Font styling patterns, variant mapping, and recommended default values for Framer property controls. ## Styling of text elements

When using Control Properties on text elements, do not introduce any new control properties for styles which can be applied with `ControlType.Font` and `FontControlDescription` respectively. Specifically:
- `FontControlDescription.defaultValue.fontSize` for `font-size`
- `FontControlDescription.defaultValue.textAlignment` for `text-alignment`
- `FontControlDescription.defaultValue.letterSpacing` for `letter-spacing`
- `FontControlDescription.defaultValue.lineHeight` for `line-height`
- `FontControlDescription.defaultValue.variant` for `font-weight` and/or `font-style`
- `FontControlDescription.defaultValue.variant` can be set only if `FontControlDescription.defaultFontType` is set to `"sans-serif"`

Remarks:
- `FontControlDescription.defaultValue.fontFamily` is not a valid default value. Font family cannot be set via `defaultValue` — it can only be selected by the user through the font control UI.
- Set `FontControlDescription.controls` to `"extended"` to expose full typography options (size, weight, spacing, alignment).

When you need to use font weight you should use `FontControlDescription.defaultValue.variant`.
The variant encapsulates both the font weight and style together. Refer to the following object to determine the correct variant for a given font weight:

```ts
interface ResolvedFontVariant {
    fontStyle: "normal" | "italic"
    weight: number
}

const variantNameToFontWeight: Record<FramerFontVariant, ResolvedFontVariant> = {
    Regular: { fontStyle: "normal", fontWeight: 400 },
    Thin: { fontStyle: "normal", fontWeight: 100 },
    "Extra Light": { fontStyle: "normal", fontWeight: 200 },
    Light: { fontStyle: "normal", fontWeight: 300 },
    Medium: { fontStyle: "normal", fontWeight: 500 },
    Semibold: { fontStyle: "normal", fontWeight: 600 },
    Bold: { fontStyle: "normal", fontWeight: 700 },
    "Extra Bold": { fontStyle: "normal", fontWeight: 800 },
    Black: { fontStyle: "normal", fontWeight: 900 },
    "Thin Italic": { fontStyle: "italic", fontWeight: 100 },
    "Extra Light Italic": { fontStyle: "italic", fontWeight: 200 },
    "Light Italic": { fontStyle: "italic", fontWeight: 300 },
    Italic: { fontStyle: "italic", fontWeight: 400 },
    "Medium Italic": { fontStyle: "italic", fontWeight: 500 },
    "Semibold Italic": { fontStyle: "italic", fontWeight: 600 },
    "Bold Italic": { fontStyle: "italic", fontWeight: 700 },
    "Extra Bold Italic": { fontStyle: "italic", fontWeight: 800 },
    "Black Italic": { fontStyle: "italic", fontWeight: 900 },
    "Regular Italic": { fontStyle: "italic", fontWeight: 400 },
}
```

Example of a simple text component in Framer which demonstrates how to use Property Control of type `ControlType.Font`.

```tsx
import { addPropertyControls, ControlType } from "framer"

/**
 * @framerSupportedLayoutWidth auto
 * @framerSupportedLayoutHeight auto
 */
export default function SimpleText(props) {
    const { label, heading } = props
    return (
        <span
            style={{
                fontSize: heading.fontSize,
                textAlign: heading.textAlign,
                fontWeight: heading.fontWeight,
                fontFamily: heading.fontFamily,
                lineHeight: heading.lineHeight,
                letterSpacing: heading.letterSpacing,
                fontStyle: heading.fontStyle,
            }}
        >
            {label}
        </span>
    )
}

addPropertyControls(SimpleText, {
    heading: {
        type: ControlType.Font,
        title: "Heading 2 Font",
        defaultValue: {
            textAlign: "right",
            fontSize: 40,
            variant: "Extra Bold",
            letterSpacing: "-0.03em",
            lineHeight: "1em",
        },
        controls: "extended",
        defaultFontType: "sans-serif",
    },
    label: {
        title: "Label",
        type: ControlType.String,
        defaultValue: "Hello",
    },
})
```

## Default Control Values

Recommended default values for common control types.

### Colors

Recommended color defaults:

```typescript
const colors: Record<string, ColorControlDescription> = {
    /** Use for main container backgrounds, cards, and primary surfaces */
    background: {
        type: ControlType.Color,
        defaultValue: "#FFFFFF", // White: backgrounds
    },
    /** Use for secondary backgrounds, input fields, and subtle visual elements */
    subtleBackground: {
        type: ControlType.Color,
        defaultValue: "#F5F5F5", // Very light gray: subtle backgrounds, placeholders
    },
    /** Use for borders, dividers, and visual separators */
    darkBackground: {
        type: ControlType.Color,
        defaultValue: "#EEEEEE", // Light gray: borders, separators
    },
    /** Use for secondary text, icons, and less prominent UI elements */
    tertiary: {
        type: ControlType.Color,
        defaultValue: "#CCCCCC", // Medium gray: text, icons
    },
    /** Use for primary text, icons, and key UI elements that need emphasis */
    primary: {
        type: ControlType.Color,
        defaultValue: "#000000", // Black: text, icons
    },
}
```

### Images

To provide a default image, set it via destructuring in the component body (`ControlType.ResponsiveImage` does not support `defaultValue`):
```tsx
const { image = { src: "https://framerusercontent.com/images/GfGkADagM4KEibNcIiRUWlfrR0.jpg", alt: "Gradient 1 - Blue" } } = props
```
When applying image properties to elements, use spreads like `{...image}`.

Recommended image sources (gradient series — use in sequence when multiple are needed):

```typescript
const images = {
  /** Use for professional or corporate contexts, informational content, or quinary image slot */
  image1: {
    src: "https://framerusercontent.com/images/GfGkADagM4KEibNcIiRUWlfrR0.jpg",
    alt: "Gradient 1 - Blue"
  },
  /** Use for creative or innovative contexts, feature highlights, or quaternary image slot */
  image2: {
    src: "https://framerusercontent.com/images/aNsAT3jCvt4zglbWCUoFe33Q.jpg",
    alt: "Gradient 2 - Purple"
  },
  /** Use for energetic contexts, call-to-action backgrounds, or tertiary image slot */
  image3: {
    src: "https://framerusercontent.com/images/BYnxEV1zjYb9bhWh1IwBZ1ZoS60.jpg",
    alt: "Gradient 3 - Orange"
  },
  /** Use for warm-toned contexts, product showcases, or secondary image slot */
  image4: {
    src: "https://framerusercontent.com/images/2uTNEj5aTl2K3NJaEFWMbnrA.jpg",
    alt: "Gradient 4 - Yellow"
  },
  /** Use for nature-themed components, environmental contexts, or primary image slot */
  image5: {
    src: "https://framerusercontent.com/images/f9RiWoNpmlCMqVRIHz8l8wYfeI.jpg",
    alt: "Gradient 5 - Green"
  }
}
```

### Typography

Use these exact font definitions for all text elements

```typescript
const typography: Record<string, FontControlDescription> = {
    /** Use for main page titles and primary headlines */
    heading1: {
        type: ControlType.Font,
        title: "Heading 1 Font",
        defaultValue: {
            fontSize: "40px",
            variant: "Bold",
            letterSpacing: "-0.04em",
            lineHeight: "1em",
        },
        controls: "extended",
        defaultFontType: "sans-serif",
    },
    /** Use for section titles and secondary headlines */
    heading2: {
        type: ControlType.Font,
        title: "Heading 2 Font",
        defaultValue: {
            fontSize: "32px",
            variant: "Semibold",
            letterSpacing: "-0.03em",
            lineHeight: "1em",
        },
        controls: "extended",
        defaultFontType: "sans-serif",
    },
    /** Use for subsection titles and feature headings */
    heading3: {
        type: ControlType.Font,
        title: "Heading 3 Font",
        defaultValue: {
            fontSize: "22px",
            variant: "Semibold",
            letterSpacing: "-0.01em",
            lineHeight: "1.2em",
        },
        controls: "extended",
        defaultFontType: "sans-serif",
    },
    /** Use for card titles, list headings, and UI element headers */
    heading4: {
        type: ControlType.Font,
        title: "Heading 4 Font",
        defaultValue: {
            fontSize: "15px",
            variant: "Medium",
            letterSpacing: "-0.01em",
            lineHeight: "1em",
        },
        controls: "extended",
        defaultFontType: "sans-serif",
    },
    /** Use for body text, descriptions, and general content */
    paragraph: {
        type: ControlType.Font,
        title: "Paragraph Font",
        defaultValue: {
            fontSize: "15px",
            variant: "Medium",
            letterSpacing: "-0.01em",
            lineHeight: "1.3em",
        },
        controls: "extended",
        defaultFontType: "sans-serif",
    },
    /** Use for buttons, links, and interactive text elements */
    buttonText: {
        type: ControlType.Font,
        title: "Button Text Font",
        defaultValue: {
            variant: "Semibold",
            fontSize: "14px",
            letterSpacing: "-0.01em",
            lineHeight: "1em",
        },
        controls: "extended",
        defaultFontType: "sans-serif",
    },
}
```

### File Types

`ControlType.File` does not support `defaultValue` in its property control. Set default values through component parameter destructuring instead.

```typescript
const fileTypes: Record<string, FileControlDescription> = {
    /** Use for image upload fields, gallery components, and avatar selectors */
    images: {
        type: ControlType.File,
        allowedFileTypes: ["jpg", "jpeg", "png", "gif", "webp", "svg"],
    },
    /** Use for video players, media galleries, and promotional content */
    videos: {
        type: ControlType.File,
        allowedFileTypes: ["mp4", "webm", "mov"],
    },
    /** Use for document viewers, file download components, and resource sections */
    documents: {
        type: ControlType.File,
        allowedFileTypes: ["pdf", "doc", "docx", "txt"],
    },
    /** Use for audio players, podcast components, and music interfaces */
    audio: {
        type: ControlType.File,
        allowedFileTypes: ["mp3", "wav", "ogg"],
    },
}
```

Use the following values for each file type as default values:

```typescript
const defaultValues: Record<keyof typeof fileTypes, string> = {
    images: "https://framerusercontent.com/images/GfGkADagM4KEibNcIiRUWlfrR0.jpg",
    videos: "https://framerusercontent.com/assets/MLWPbW1dUQawJLhhun3dBwpgJak.mp4",
    audio: "https://framerusercontent.com/assets/8w3IUatLX9a5JVJ6XPCVuHi94.mp3",
}
```

Recommended pattern for file control defaults:

```tsx
function MyComponent(props) {
    // CORRECT: Set file defaults through parameter destructuring
    const {
        imageFile = "https://framerusercontent.com/images/GfGkADagM4KEibNcIiRUWlfrR0.jpg",
        videoFile = "https://framerusercontent.com/assets/MLWPbW1dUQawJLhhun3dBwpgJak.mp4",
        audioFile = "https://framerusercontent.com/assets/8w3IUatLX9a5JVJ6XPCVuHi94.mp3",
    } = props

    return (
        <div>
            <img src={imageFile} />
            <video src={videoFile} />
            <audio src={audioFile} />
        </div>
    )
}

addPropertyControls(MyComponent, {
    imageFile: {
        type: ControlType.File,
        allowedFileTypes: ["jpg", "jpeg", "png", "gif", "webp", "svg"],
    },
    // Additional file controls...
})
```

# Framer Code Component Examples

Reference implementations demonstrating best practices for code components.

## Cookie Banner

Location-aware cookie consent with timezone detection.

```tsx
// Cookie banner with opt-in for Europe, opt-out elsewhere, based on time zone
import {
  useEffect,
  useState,
  startTransition,
  type CSSProperties,
} from "react";
import { addPropertyControls, ControlType, RenderTarget } from "framer";

interface CookiebannerProps {
  message: string;
  acceptLabel: string;
  declineLabel: string;
  backgroundColor: string;
  textColor: string;
  buttonColor: string;
  buttonTextColor: string;
  font: any;
  borderRadius: number;
  buttonFont: any;
  style?: CSSProperties;
}

/**
 * Cookies
 *
 * @framerIntrinsicWidth 400
 * @framerIntrinsicHeight 100
 *
 * @framerSupportedLayoutWidth any-prefer-fixed
 * @framerSupportedLayoutHeight any-prefer-fixed
 */
export default function Cookiebanner(props: CookiebannerProps) {
  const {
    message,
    acceptLabel,
    declineLabel,
    backgroundColor,
    textColor,
    buttonColor,
    buttonTextColor,
    font,
    borderRadius,
  } = props;

  // Guess if user is in Europe based on timezone offset
  const [show, setShow] = useState(true);
  const [isEurope, setIsEurope] = useState(false);
  useEffect(() => {
    if (typeof window !== "undefined") {
      const offset = new Date().getTimezoneOffset();
      // Europe: UTC+0 to UTC+3 (offset -0 to -180)
      startTransition(() => setIsEurope(offset <= 0 && offset >= -180));
    }
  }, []);

  // Hide on accept/decline
  function handleAccept() {
    startTransition(() => setShow(false));
  }
  function handleDecline() {
    startTransition(() => setShow(false));
  }

  if (!show || RenderTarget.current() === RenderTarget.thumbnail) return null;

  const buttonBaseStyles = {
    borderRadius: 10,
    flex: 1,
    border: `1px solid ${buttonColor}`,
    padding: "8px 18px",
    cursor: "pointer",
    ...props.buttonFont,
  };

  const isFixedWidth = props?.style && props.style.width === "100%";

  return (
    <div
      style={{
        ...props.style,
        overflow: "hidden",
        position: "relative",
        ...(isFixedWidth ? { ...props?.style } : { minWidth: "max-content" }),
        background: backgroundColor,
        color: textColor,
        borderRadius,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        padding: 20,
        boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
        gap: 20,

        ...props.font,
      }}
    >
      <span style={{ flex: 1 }}>{message}</span>
      <div style={{ width: "100%", display: "flex", gap: 10 }}>
        <button
          style={{
            ...buttonBaseStyles,
            background: "transparent",
            color: buttonColor,
          }}
          onClick={handleDecline}
        >
          {declineLabel}
        </button>
        <button
          style={{
            ...buttonBaseStyles,
            background: buttonColor,
            color: buttonTextColor,
          }}
          onClick={handleAccept}
        >
          {acceptLabel}
        </button>
      </div>
    </div>
  );
}

addPropertyControls(Cookiebanner, {
  message: {
    type: ControlType.String,
    title: "Message",
    defaultValue: "We use cookies to improve your website experience.",
    displayTextArea: true,
  },
  acceptLabel: {
    type: ControlType.String,
    title: "Accept Label",
    defaultValue: "Accept",
  },
  declineLabel: {
    type: ControlType.String,
    title: "Decline Label",
    defaultValue: "Decline",
  },
  backgroundColor: {
    type: ControlType.Color,
    title: "Background",
    defaultValue: "#fff",
  },
  textColor: {
    type: ControlType.Color,
    title: "Text Color",
    defaultValue: "#222",
  },
  buttonColor: {
    type: ControlType.Color,
    title: "Button Color",
    defaultValue: "#111",
  },
  buttonTextColor: {
    type: ControlType.Color,
    title: "Button Text",
    defaultValue: "#fff",
  },
  font: {
    type: ControlType.Font,
    title: "Font",
    controls: "extended",
    defaultFontType: "sans-serif",
    defaultValue: {
      variant: "Medium",
      fontSize: "14px",
      letterSpacing: "-0.01em",
      lineHeight: "1em",
    },
  },
  buttonFont: {
    type: ControlType.Font,
    title: "Font",
    controls: "extended",
    defaultFontType: "sans-serif",
    defaultValue: {
      variant: "Medium",
      fontSize: "14px",
      letterSpacing: "-0.01em",
      lineHeight: "1em",
    },
  },
  borderRadius: {
    type: ControlType.Number,
    title: "Radius",
    defaultValue: 8,
    min: 0,
    max: 32,
  },
});
```

## Tweemoji

Convert emoji to Twitter's Twemoji SVGs.

````tsx
import { useMemo, useEffect, useState, type CSSProperties } from "react";
import { addPropertyControls, ControlType, withCSS } from "framer";
import twemojiParser from "https://jspm.dev/twemoji-parser@14.0.0";

const fireSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/twemoji/14.0.2/svg/1f525.svg";

interface TwemojiProps {
  /** Emoji to convert such as 🍐, 🐙 or 🐸 */
  search?: string;
  isSelection?: boolean;
  [prop: string]: any;
}

const baseURL = "https://cdnjs.cloudflare.com/ajax/libs/twemoji/14.0.2/svg/";

/**
 * TWEMOJI
 *
 * Convert any emoji into a Twemoji from Twitter. Choose a preset or type in your emoji and the Twemoji will automatically appear on the canvas.
 *
 * ```jsx
 * <Twemoji search="🍐" />
 * ```
 *
 * @framerIntrinsicWidth 100
 * @framerIntrinsicHeight 100
 *
 * @framerSupportedLayoutWidth fixed
 * @framerSupportedLayoutHeight fixed
 */
export default function Twemoji(props: TwemojiProps) {
  const { search, isSelection, selection, style, alt = "" } = props;

  const emoji = useMemo(() => {
    if (isSelection) return selection;
    if (!search) return "⭐️";
    return search;
  }, [search, isSelection, selection]);

  const src = useMemo(() => {
    const parsedTwemoji = twemojiParser.parse(emoji, {
      buildUrl: (icon) => `${baseURL}${icon}.svg`,
    });
    return parsedTwemoji[0].url;
  }, [emoji]);

  return (
    <div style={containerStyle}>
      <img src={src} style={containerStyle} alt={alt} />
    </div>
  );
}

addPropertyControls<TwemojiProps>(Twemoji, {
  isSelection: {
    type: ControlType.Boolean,
    title: "Select",
    enabledTitle: "Preset",
    disabledTitle: "Search",
  },
  selection: {
    type: ControlType.Enum,
    title: " ",
    options: ["🔥", "💖", "😆", "👍", "👎"],
    defaultValue: "🔥",
    displaySegmentedControl: true,
    hidden: ({ isSelection }) => !isSelection,
  },
  search: {
    type: ControlType.String,
    title: " ",
    placeholder: "Paste Emoji…",
    defaultValue: "⭐️",
    hidden: ({ isSelection }) => isSelection,
  },
});

const containerStyle: CSSProperties = {
  height: "100%",
  width: "100%",
  objectFit: "contain",
  textAlign: "center",
  overflow: "hidden",
  backgroundColor: "transparent",
};
````

## Image Compare

Before/after image comparison slider.

```tsx
import {
  addPropertyControls,
  ControlType,
  RenderTarget,
  useIsStaticRenderer,
} from "framer";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  startTransition,
  type CSSProperties,
} from "react";

interface Image {
  src: string;
  alt: string;
}

interface ImageCompareProps {
  beforeImage: Image;
  afterImage: Image;
  orientation: "horizontal" | "vertical";
  initialPosition: number;
  dividerColor: string;
  dividerWidth: number;
  dividerShadow: boolean;
  showHandle: boolean;
  handleColor: string;
  handleSize: number;
  style?: CSSProperties;
}

/**
 * Image Comparison Slider
 *
 * A component that allows users to compare two images by dragging a divider.
 *
 * @framerIntrinsicWidth 500
 * @framerIntrinsicHeight 300
 *
 * @framerSupportedLayoutWidth fixed
 * @framerSupportedLayoutHeight fixed
 */
export default function ImageCompare(props: ImageCompareProps) {
  const {
    beforeImage = {
      src: "https://framerusercontent.com/images/GfGkADagM4KEibNcIiRUWlfrR0.jpg",
      alt: "Before image",
    },
    afterImage = {
      src: "https://framerusercontent.com/images/aNsAT3jCvt4zglbWCUoFe33Q.jpg",
      alt: "After image",
    },
    orientation = "horizontal",
    initialPosition = 50,
    dividerColor = "#FFFFFF",
    dividerWidth = 2,
    dividerShadow = true,
    showHandle = false,
    handleColor = "#FFFFFF",
    handleSize = 40,
  } = props;

  const isHorizontal = orientation === "horizontal";
  const containerRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState(initialPosition);
  const [isDragging, setIsDragging] = useState(false);
  const isStatic = useIsStaticRenderer();

  const updatePositionFromEvent = useCallback(
    (e) => {
      if (!containerRef.current) return;

      const rect = containerRef.current.getBoundingClientRect();

      if (isHorizontal) {
        const x = e.clientX - rect.left;
        const newPosition = Math.max(0, Math.min(100, (x / rect.width) * 100));
        startTransition(() => setPosition(newPosition));
      } else {
        const y = e.clientY - rect.top;
        const newPosition = Math.max(0, Math.min(100, (y / rect.height) * 100));
        startTransition(() => setPosition(newPosition));
      }
    },
    [isHorizontal],
  );

  const handleClick = useCallback(
    (e) => {
      // Only handle as a click if we're not dragging
      if (!isDragging) {
        updatePositionFromEvent(e);
      }
    },
    [isDragging, updatePositionFromEvent],
  );

  const handleDoubleClick = () => {
    startTransition(() => setPosition(initialPosition));
  };

  const handleMouseDown = (e) => {
    e.preventDefault();
    startTransition(() => setIsDragging(true));
  };

  const handleMouseMove = useCallback(
    (e) => {
      if (!isDragging || !containerRef.current) return;
      updatePositionFromEvent(e);
    },
    [isDragging, updatePositionFromEvent],
  );

  const handleMouseUp = useCallback(() => {
    startTransition(() => setIsDragging(false));
  }, []);

  // Add global event listeners for drag
  useEffect(() => {
    if (isStatic) return;

    const handleGlobalMouseMove = (e) => handleMouseMove(e);
    const handleGlobalMouseUp = () => handleMouseUp();

    if (isDragging) {
      window.addEventListener("mousemove", handleGlobalMouseMove);
      window.addEventListener("mouseup", handleGlobalMouseUp);
    }

    return () => {
      window.removeEventListener("mousemove", handleGlobalMouseMove);
      window.removeEventListener("mouseup", handleGlobalMouseUp);
    };
  }, [isDragging, handleMouseMove, handleMouseUp, isStatic]);

  return (
    <div
      ref={containerRef}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        cursor: isDragging
          ? isHorizontal
            ? "ew-resize"
            : "ns-resize"
          : "pointer",
        userSelect: "none",
      }}
      onClick={isStatic ? undefined : handleClick}
      onMouseMove={isStatic ? undefined : handleMouseMove}
      onMouseDown={isStatic ? undefined : handleMouseDown}
      onMouseUp={isStatic ? undefined : handleMouseUp}
      onDoubleClick={handleDoubleClick}
      onKeyDown={(e) => {
        if (e.key === " " || e.key === "Enter") {
          handleClick(e);
        }
      }}
      tabIndex={0}
      role="slider"
      aria-valuenow={position}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-orientation={orientation}
    >
      {/* After Image (Full) */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
          backgroundImage: `url(${afterImage.src})`,
          backgroundSize: "cover",
          backgroundPosition: "center",
        }}
        aria-label={afterImage.alt}
        role="img"
      />

      {/* Before Image (Clipped) */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
          backgroundImage: `url(${beforeImage.src})`,
          backgroundSize: "cover",
          backgroundPosition: "center",
          clipPath: isHorizontal
            ? `inset(0 ${100 - position}% 0 0)`
            : `inset(0 0 ${100 - position}% 0)`,
        }}
        aria-label={beforeImage.alt}
        role="img"
      />

      {/* Divider */}
      <div
        style={{
          position: "absolute",
          top: isHorizontal ? 0 : `${position}%`,
          left: isHorizontal ? `${position}%` : 0,
          width: isHorizontal ? `${dividerWidth}px` : "100%",
          height: isHorizontal ? "100%" : `${dividerWidth}px`,
          backgroundColor: dividerColor,
          boxShadow: dividerShadow ? "0 0 5px rgba(0, 0, 0, 0.7)" : "none",
          transform: isHorizontal
            ? `translateX(-${dividerWidth / 2}px)`
            : `translateY(-${dividerWidth / 2}px)`,
          cursor: isHorizontal ? "ew-resize" : "ns-resize",
          zIndex: 2,
        }}
        onMouseDown={isStatic ? undefined : handleMouseDown}
      />

      {/* Handle */}
      {showHandle && (
        <div
          style={{
            position: "absolute",
            top: isHorizontal
              ? `calc(50% - ${handleSize / 2}px)`
              : `${position}%`,
            left: isHorizontal
              ? `${position}%`
              : `calc(50% - ${handleSize / 2}px)`,
            width: `${handleSize}px`,
            height: `${handleSize}px`,
            borderRadius: "50%",
            backgroundColor: handleColor,
            border: `2px solid ${handleColor}`,
            boxShadow: "0 0 5px rgba(0, 0, 0, 0.5)",
            transform: isHorizontal
              ? `translateX(-${handleSize / 2}px)`
              : `translateY(-${handleSize / 2}px)`,
            cursor: isHorizontal ? "ew-resize" : "ns-resize",
            zIndex: 3,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}
          onMouseDown={isStatic ? undefined : handleMouseDown}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              width: "100%",
              height: "100%",
              transform: isHorizontal ? "rotate(90deg)" : "rotate(0deg)",
            }}
          >
            <svg
              viewBox="0 0 24 24"
              width={handleSize * 0.5}
              height={handleSize * 0.5}
              strokeWidth="2"
              stroke="#000"
              fill="none"
              aria-label="Drag handle"
            >
              <title>Drag handle</title>
              <path d="M13 5l6 6m-6 6l6-6m-6 0l-6 6m6-6l-6-6" />
            </svg>
          </div>
        </div>
      )}
    </div>
  );
}

addPropertyControls(ImageCompare, {
  beforeImage: {
    type: ControlType.ResponsiveImage,
    title: "Before Image",
  },
  afterImage: {
    type: ControlType.ResponsiveImage,
    title: "After Image",
  },
  orientation: {
    type: ControlType.Enum,
    title: "Orientation",
    options: ["horizontal", "vertical"],
    optionTitles: ["Horizontal", "Vertical"],
    defaultValue: "horizontal",
    displaySegmentedControl: true,
  },
  initialPosition: {
    type: ControlType.Number,
    title: "Initial Position",
    defaultValue: 50,
    min: 0,
    max: 100,
    step: 1,
    unit: "%",
  },
  dividerColor: {
    type: ControlType.Color,
    title: "Divider Color",
    defaultValue: "#FFFFFF",
  },
  dividerWidth: {
    type: ControlType.Number,
    title: "Divider Width",
    defaultValue: 2,
    min: 1,
    max: 20,
    step: 1,
    unit: "px",
  },
  dividerShadow: {
    type: ControlType.Boolean,
    title: "Divider Shadow",
    defaultValue: true,
    enabledTitle: "On",
    disabledTitle: "Off",
  },
  showHandle: {
    type: ControlType.Boolean,
    title: "Show Handle",
    defaultValue: false,
    enabledTitle: "Show",
    disabledTitle: "Hide",
  },
  handleColor: {
    type: ControlType.Color,
    title: "Handle Color",
    defaultValue: "#FFFFFF",
    hidden: ({ showHandle }) => !showHandle,
  },
  handleSize: {
    type: ControlType.Number,
    title: "Handle Size",
    defaultValue: 40,
    min: 20,
    max: 80,
    step: 1,
    unit: "px",
    hidden: ({ showHandle }) => !showHandle,
  },
});
```

## Notes (Sticky Note)

Colorful sticky note with font options.

```tsx
import { type MouseEventHandler, type CSSProperties, useMemo } from "react";
import { addPropertyControls, ControlType, RenderTarget, Color } from "framer";

const colors = {
  blue: "#0099FF",
  darkBlue: "#0066FF",
  purple: "#8855FF",
  red: "#FF5588",
  green: "#22CC66",
  yellow: "#FFBB00",
};

interface NotesProps {
  note: string;
  shadow: boolean;
  color: string;
  preview: boolean;
  alignment: "left" | "center" | "right";
  smallFont: boolean;
  onClick?: MouseEventHandler<HTMLDivElement>;
  onMouseEnter?: MouseEventHandler<HTMLDivElement>;
  onMouseLeave?: MouseEventHandler<HTMLDivElement>;
  onMouseDown?: MouseEventHandler<HTMLDivElement>;
  onMouseUp?: MouseEventHandler<HTMLDivElement>;
  useScriptFont: boolean;
  font: CSSProperties;
}

/**
 * STICKY
 *
 * @framerIntrinsicWidth 150
 * @framerIntrinsicHeight 150
 *
 * @framerSupportedLayoutWidth any-prefer-fixed
 * @framerSupportedLayoutHeight any-prefer-fixed
 */
export default function Notes(props: NotesProps) {
  const {
    note = "",
    shadow,
    color,
    preview,
    alignment,
    smallFont,
    onClick,
    onMouseEnter,
    onMouseLeave,
    onMouseDown,
    onMouseUp,
    useScriptFont,
    font,
  } = props;

  const [baseColorString, backgroundColorString] = useMemo(() => {
    const baseColor = Color(colors[color]);
    const hslColor = Color.toHsl(baseColor);
    hslColor.l = 0.95;

    const baseColorString = Color(colors[color]).toValue();
    const backgroundColorString = Color(hslColor).toValue();

    return [baseColorString, backgroundColorString];
  }, [color]);

  const centerAligned = alignment === "center";
  const hasContent = note.length > 0;

  return (
    <div
      style={{
        flex: 1,
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: centerAligned ? "center" : "flex-start",
        backgroundColor: backgroundColorString,
        overflow: "hidden",
        paddingLeft: smallFont ? 15 : 18,
        paddingTop: useScriptFont ? 12 : 14,
        paddingBottom: useScriptFont ? 12 : 14,
        paddingRight: smallFont ? 15 : 18,
        borderRadius: 8,
        visibility:
          RenderTarget.current() === RenderTarget.preview && !preview
            ? "hidden"
            : "visible",
        ...(useScriptFont ? { fontFamily: "Nanum Pen Script" } : font),
        //@ts-ignore
        fontDisplay: "fallback",
        boxShadow: shadow ? "0 4px 10px rgba(0,0,0,0.08)" : "none",
      }}
      {...{ onClick, onMouseEnter, onMouseLeave, onMouseDown, onMouseUp }}
    >
      {useScriptFont && (
        <link
          href="https://fonts.googleapis.com/css?family=Nanum+Pen+Script&display=swap"
          rel="stylesheet"
        />
      )}
      <p
        style={{
          width: "max-content",
          wordBreak: "break-word",
          overflowWrap: "break-word",
          overflow: "hidden",
          whiteSpace: "pre-wrap",
          margin: 0,
          fontSize: smallFont
            ? useScriptFont
              ? 18
              : 12
            : useScriptFont
              ? 32
              : 24,

          lineHeight: smallFont
            ? useScriptFont
              ? 1.15
              : 1.4
            : useScriptFont
              ? 1.08
              : 1.3,
          textAlign: alignment,
          color: baseColorString,
          display: "-webkit-box",
          opacity: hasContent ? 1 : 0.5,
          WebkitBoxOrient: "vertical",
        }}
      >
        {hasContent ? note : "Write something..."}
      </p>
    </div>
  );
}

addPropertyControls(Notes, {
  note: {
    type: ControlType.String,
    displayTextArea: true,
    placeholder: `Write something… \n\n\n`,
  },
  color: {
    type: ControlType.Enum,
    defaultValue: "blue",
    options: Object.keys(colors),
    optionTitles: Object.keys(colors).map((c) =>
      c.replace(/^\w/, (c) => c.toUpperCase()),
    ),
  },

  alignment: {
    title: "Text Align",
    type: ControlType.Enum,
    displaySegmentedControl: true,
    optionTitles: ["Left", "Center", "Right"],
    options: ["left", "center", "right"],
  },
  useScriptFont: {
    type: ControlType.Boolean,
    disabledTitle: "Custom",
    enabledTitle: "Script",
    title: "Font",
    defaultTitle: true,
  },
  font: {
    type: ControlType.Font,
    defaultFontType: "sans-serif",
    controls: "extended",
    hidden: ({ useScriptFont }) => useScriptFont,
  },
  smallFont: {
    type: ControlType.Boolean,
    disabledTitle: "Big",
    enabledTitle: "Small",
    title: "Text Size",
    defaultValue: true,
  },
  preview: {
    type: ControlType.Boolean,
    defaultValue: true,
    title: "In Preview",
    enabledTitle: "Show",
    disabledTitle: "Hide",
  },
  shadow: {
    type: ControlType.Boolean,
    defaultValue: false,
    title: "Shadow",
    enabledTitle: "Show",
    disabledTitle: "Hide",
  },
});

Notes.displayName = "Sticky Note";
```

## Key Patterns Demonstrated

1. **SSR Safety**: `if (typeof window !== "undefined")` guards
2. **State Transitions**: `setState` wrapped in `startTransition()` for smooth interactions
3. **Static Renderer**: `useIsStaticRenderer()` to skip animations on canvas
4. **Image Defaults**: Set in destructuring, not in property controls
5. **Font Controls**: `controls: "extended"` with `defaultFontType: "sans-serif"` for full typography customization
6. **Conditional Controls**: `hidden: (props) => !props.showFeature`
7. **Accessibility**: `role`, `aria-*`, semantic HTML, keyboard support
8. **Color Utilities**: Using `Color` from framer for color manipulation
