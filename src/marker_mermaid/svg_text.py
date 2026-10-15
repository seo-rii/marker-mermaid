"""Conservative static SVG text projection for OCR scoring.

This is a visibility model, not a layout engine. Unsupported conditional CSS,
variables and dynamic SVG return unavailable instead of claiming painted text.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from itertools import product

import cssselect2
import tinycss2
from tinycss2.color3 import parse_color

_VISIBILITY_PROPERTIES = {"display", "visibility", "opacity"}
_PAINT_DEFAULTS = {
    "fill": "black",
    "stroke": "none",
    "color": "black",
    "fill-opacity": "1",
    "stroke-opacity": "1",
    "font-size": "16px",
    "stroke-width": "1px",
}
_PAINT_EFFECTS = {"clip-path", "mask", "filter", "content-visibility", "all"}
_PROPERTIES = _VISIBILITY_PROPERTIES | _PAINT_DEFAULTS.keys() | _PAINT_EFFECTS
_NON_PAINTED = {"defs", "clippath", "mask", "marker", "pattern", "symbol", "title", "desc", "style"}
_DYNAMIC = {
    "animate",
    "animatemotion",
    "animatetransform",
    "set",
    "discard",
    "use",
    "foreignobject",
}
_MAX_ELEMENTS = 10_000
_MAX_SELECTORS = 2_000
_DYNAMIC_PSEUDO = re.compile(
    r":(?:hover|active|focus(?:-within|-visible)?|visited|target|checked|enabled|"
    r"disabled|indeterminate|valid|invalid|in-range|out-of-range|placeholder-shown|"
    r"autofill|open|modal|fullscreen|playing|paused)\b",
    re.IGNORECASE,
)


def _projection_selectors(prelude):
    selector_text = tinycss2.serialize(prelude)
    dynamic = list(_DYNAMIC_PSEUDO.finditer(selector_text))
    if len(dynamic) > 4:
        raise ValueError("dynamic selector expansion exceeds projection budget")
    # Consider both states, including negation and :has(), instead of letting
    # cssselect2 silently treat dynamic pseudo-classes as never matching.
    for states in product((False, True), repeat=len(dynamic)):
        fragments = []
        offset = 0
        for match, state in zip(dynamic, states, strict=True):
            fragments.extend(
                (selector_text[offset : match.start()], ":is(*)" if state else ":not(*)")
            )
            offset = match.end()
        fragments.append(selector_text[offset:])
        for selector in cssselect2.compile_selector_list("".join(fragments)):
            yield selector, bool(dynamic)


def _declarations(css):
    declarations = tinycss2.parse_declaration_list(css, skip_comments=True, skip_whitespace=True)
    result = []
    for declaration in declarations:
        if declaration.type != "declaration":
            raise ValueError("unsupported CSS declaration")
        if declaration.lower_name in _PROPERTIES:
            value = [token for token in declaration.value if token.type != "whitespace"]
            if declaration.lower_name in _VISIBILITY_PROPERTIES:
                if len(value) != 1 or value[0].type not in {"ident", "number", "percentage"}:
                    raise ValueError("unsupported CSS visibility value")
                value = value[0]
            result.append((declaration.lower_name, value, declaration.important))
    return result


def _keyword(token, *, inherited, initial):
    if token.type != "ident":
        raise ValueError("expected a visibility keyword")
    value = token.lower_value
    if value in {"inherit", "unset"}:
        return inherited
    if value == "initial":
        return initial
    return value


def _paint_opacity(value: str) -> float:
    token = tinycss2.parse_one_component_value(value)
    if token.type not in {"number", "percentage"} or not math.isfinite(token.value):
        raise ValueError("unsupported paint opacity")
    return min(1, max(0, token.value / (100 if token.type == "percentage" else 1)))


def _paint_length(value: str, inherited: float) -> float:
    token = tinycss2.parse_one_component_value(value)
    if token.type == "ident" and token.lower_value in {
        "xx-small",
        "x-small",
        "small",
        "medium",
        "large",
        "x-large",
        "xx-large",
    }:
        return 16.0
    if token.type not in {"number", "dimension", "percentage"} or not math.isfinite(token.value):
        raise ValueError("unsupported paint length")
    if token.type == "percentage":
        return max(0, inherited * token.value / 100)
    if token.type == "dimension" and token.lower_unit in {"em", "ex", "ch", "lh"}:
        return max(0, inherited * token.value)
    return max(0, token.value)


def _text_is_painted(paint: dict[str, str], font_size: float, stroke_width: float) -> bool:
    if font_size == 0:
        return False
    color = parse_color(paint["color"])
    if color is None or color == "currentColor":
        raise ValueError("unsupported text color")
    for name in ("fill", "stroke"):
        if paint[name] == "none" or (name == "stroke" and stroke_width == 0):
            continue
        value = parse_color(paint[name])
        if value == "currentColor":
            value = color
        if value is None:
            raise ValueError("unsupported text paint")
        if value.alpha * _paint_opacity(paint[f"{name}-opacity"]) > 0:
            return True
    return False


def svg_visible_texts(svg: str) -> list[str] | None:
    """Return visible text runs under a supported static CSS cascade.

    Presentation attributes, selector specificity, inline styles and !important
    are honored. Display/opacity suppress entire subtrees; visibility inherits
    but can be restored by descendants. Unknown CSS visibility constructs make
    projection unavailable, which the pipeline treats as missing evidence.
    """
    if type(svg) is not str:
        return None
    try:
        root = ET.fromstring(svg)
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            return None
        elements = list(root.iter())
        if len(elements) > _MAX_ELEMENTS:
            return None
        matcher = cssselect2.Matcher()
        selector_count = 0
        for element in elements:
            tag = element.tag.rsplit("}", 1)[-1].casefold()
            if tag in _DYNAMIC:
                return None
            if tag != "style":
                continue
            if (element.get("media") or "").strip().casefold() not in {"", "all"}:
                return None
            rules = tinycss2.parse_stylesheet(
                "".join(element.itertext()),
                skip_comments=True,
                skip_whitespace=True,
            )
            for rule in rules:
                # Conditional rules require a browser/media environment. Never
                # silently drop a stylesheet that could hide its labels.
                if rule.type == "at-rule" and rule.lower_at_keyword in {
                    "keyframes",
                    "-webkit-keyframes",
                }:
                    frames = tinycss2.parse_rule_list(
                        rule.content or [],
                        skip_comments=True,
                        skip_whitespace=True,
                    )
                    if any(
                        frame.type != "qualified-rule" or _declarations(frame.content)
                        for frame in frames
                    ):
                        return None
                    # Mermaid emits unused stroke-dashoffset keyframes even for
                    # static diagrams. They cannot change this visibility model.
                    continue
                if rule.type != "qualified-rule":
                    return None
                declarations = _declarations(rule.content)
                if not declarations:
                    continue
                for selector, dynamic in _projection_selectors(rule.prelude):
                    selector_count += 1
                    if selector_count > _MAX_SELECTORS or selector.pseudo_element is not None:
                        return None
                    matcher.add_selector(selector, (declarations, dynamic))

        states = {}
        paints = {}
        for wrapped in cssselect2.ElementWrapper.from_xml_root(root).iter_subtree():
            element = wrapped.etree_element
            parent = wrapped.parent.etree_element if wrapped.parent else None
            parent_suppressed, parent_visibility, parent_opacity, parent_uncertain = states.get(
                parent,
                (False, "visible", 1.0, False),
            )
            parent_paint, parent_font_size, parent_stroke_width = paints.get(
                parent,
                (_PAINT_DEFAULTS, 16.0, 1.0),
            )
            tag = wrapped.local_name.casefold()
            # Cascade key: importance, inline specificity, selector specificity,
            # source order. Presentation attributes precede author CSS.
            values = {}
            dynamic_unavailable = False
            for name in _PROPERTIES:
                if (attribute := element.get(name)) is not None:
                    for prop, token, important in _declarations(f"{name}:{attribute}"):
                        if important:
                            return None
                        values[prop] = ((False, 0, (0, 0, 0), 0), token)
            for specificity, order, _pseudo, payload in matcher.match(wrapped):
                declarations, dynamic = payload
                if dynamic:
                    dynamic_unavailable = True
                    continue
                for prop, token, important in declarations:
                    key = (important, 0, specificity, order)
                    if prop not in values or key >= values[prop][0]:
                        values[prop] = (key, token)
            for index, (prop, token, important) in enumerate(
                _declarations(element.get("style", ""))
            ):
                key = (important, 1, (0, 0, 0), index)
                if prop not in values or key >= values[prop][0]:
                    values[prop] = (key, token)

            display = "inline"
            visibility = parent_visibility
            opacity = 1.0
            if "display" in values:
                display = _keyword(values["display"][1], inherited="inline", initial="inline")
                if display not in {"none", "inline", "block", "contents", "inline-block"}:
                    return None
            if "visibility" in values:
                visibility = _keyword(
                    values["visibility"][1],
                    inherited=parent_visibility,
                    initial="visible",
                )
                if visibility not in {"visible", "hidden", "collapse"}:
                    return None
            if "opacity" in values:
                token = values["opacity"][1]
                if token.type == "ident":
                    keyword = token.lower_value
                    if keyword == "inherit":
                        opacity = parent_opacity
                    elif keyword not in {"initial", "unset"}:
                        return None
                else:
                    opacity = token.value / (100 if token.type == "percentage" else 1)
                if not math.isfinite(opacity):
                    return None
                opacity = min(1, max(0, opacity))
            suppressed = (
                parent_suppressed or tag in _NON_PAINTED or display == "none" or opacity == 0
            )
            paint = dict(parent_paint)
            uncertain = parent_uncertain or dynamic_unavailable
            for name in _PAINT_DEFAULTS:
                if name in values:
                    value = tinycss2.serialize(values[name][1]).strip()
                    if value.casefold() in {"inherit", "unset"}:
                        value = parent_paint[name]
                    elif value.casefold() == "initial":
                        value = _PAINT_DEFAULTS[name]
                    paint[name] = value
            font_size = _paint_length(paint["font-size"], parent_font_size)
            stroke_width = _paint_length(paint["stroke-width"], parent_stroke_width)
            for name in _PAINT_EFFECTS:
                if name in values:
                    value = tinycss2.serialize(values[name][1]).strip().casefold()
                    if name == "content-visibility":
                        if value == "hidden":
                            suppressed = True
                        elif value not in {"visible", "initial"}:
                            uncertain = True
                    elif name == "all" or value not in {"none", "initial", "unset"}:
                        uncertain = True
            states[element] = (suppressed, visibility, opacity, uncertain)
            paints[element] = (paint, font_size, stroke_width)
        # Traverse each text terminal in document order for correctly placed
        # tails, including tails after nested hidden spans.
        result = []
        for element in elements:
            if element.tag.rsplit("}", 1)[-1].casefold() != "text":
                continue
            fragments = []
            events = [(element, None)]
            while events:
                item, tail_parent = events.pop()
                if tail_parent is not None:
                    parent_state = states[tail_parent]
                    if not parent_state[0] and parent_state[1] == "visible":
                        if parent_state[3]:
                            return None
                        paint, font_size, stroke_width = paints[tail_parent]
                        if _text_is_painted(paint, font_size, stroke_width):
                            fragments.append(item.tail or "")
                    continue
                hidden, visibility, _opacity, uncertain = states[item]
                paint, font_size, stroke_width = paints[item]
                if not hidden and visibility == "visible":
                    if uncertain:
                        return None
                    painted = _text_is_painted(paint, font_size, stroke_width)
                else:
                    painted = False
                fragments.append((item.text or "") if painted else " ")
                for child in reversed(item):
                    # Tail inherits the current element's paint state.
                    events.append((child, item))
                    events.append((child, None))
            text = "".join(fragments).replace("\u200b", "")
            if text.strip():
                result.append(text)
        return result
    except (ET.ParseError, RecursionError, ValueError, cssselect2.SelectorError):
        return None
