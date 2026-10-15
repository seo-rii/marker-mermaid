"""Conservative static SVG text projection for OCR scoring.

This is a visibility model, not a layout engine. Unsupported conditional CSS,
variables and dynamic SVG return unavailable instead of claiming painted text.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

import cssselect2
import tinycss2

_PROPERTIES = {"display", "visibility", "opacity"}
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


def _declarations(css):
    declarations = tinycss2.parse_declaration_list(css, skip_comments=True, skip_whitespace=True)
    result = []
    for declaration in declarations:
        if declaration.type != "declaration":
            raise ValueError("unsupported CSS declaration")
        if declaration.lower_name in _PROPERTIES:
            value = [token for token in declaration.value if token.type != "whitespace"]
            if len(value) != 1 or value[0].type not in {"ident", "number", "percentage"}:
                raise ValueError("unsupported CSS visibility value")
            result.append((declaration.lower_name, value[0], declaration.important))
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
                selectors = cssselect2.compile_selector_list(rule.prelude)
                selector_count += len(selectors)
                if selector_count > _MAX_SELECTORS:
                    return None
                for selector in selectors:
                    if selector.pseudo_element is not None:
                        return None
                    matcher.add_selector(selector, declarations)

        states = {}
        for wrapped in cssselect2.ElementWrapper.from_xml_root(root).iter_subtree():
            element = wrapped.etree_element
            parent = wrapped.parent.etree_element if wrapped.parent else None
            parent_suppressed, parent_visibility, parent_opacity = states.get(
                parent,
                (False, "visible", 1.0),
            )
            tag = wrapped.local_name.casefold()
            # Cascade key: importance, inline specificity, selector specificity,
            # source order. Presentation attributes precede author CSS.
            values = {}
            for name in _PROPERTIES:
                if (attribute := element.get(name)) is not None:
                    for prop, token, important in _declarations(f"{name}:{attribute}"):
                        if important:
                            return None
                        values[prop] = ((False, 0, (0, 0, 0), 0), token)
            for specificity, order, _pseudo, declarations in matcher.match(wrapped):
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
            states[element] = (suppressed, visibility, opacity)
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
                        fragments.append(item.tail or "")
                    continue
                hidden, visibility, _opacity = states[item]
                fragments.append(
                    (item.text or "") if not hidden and visibility == "visible" else " "
                )
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
