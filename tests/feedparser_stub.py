import email.utils
from types import SimpleNamespace
import xml.etree.ElementTree as ET


def _local_name(tag: str) -> str:
    return str(tag or "").split("}", 1)[-1]


class AttrDict(dict):
    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


def _find_child(node, *names):
    for child in list(node):
        if _local_name(child.tag) in names:
            return child
    return None


def _find_children(node, *names):
    return [child for child in list(node) if _local_name(child.tag) in names]


def _child_text(node, *names):
    child = _find_child(node, *names)
    if child is None:
        return ""
    return "".join(child.itertext()).strip()


def _parsed_time(value: str):
    return email.utils.parsedate(value) if value else None


def parse(content, *_args, **_kwargs):
    if isinstance(content, (bytes, bytearray)):
        head = bytes(content[:120]).lower()
        if b"encoding=\"gbk\"" in head or b"encoding='gbk'" in head:
            content = bytes(content).decode("gbk", errors="replace")
        else:
            content = bytes(content).decode("utf-8", errors="replace")

    if isinstance(content, str) and content.strip().startswith(("http://", "https://")):
        return SimpleNamespace(
            bozo=False,
            entries=[],
            feed=SimpleNamespace(title="", link=str(content).strip()),
        )

    root = ET.fromstring(content)

    if _local_name(root.tag) == "feed":
        feed = SimpleNamespace(
            title=_child_text(root, "title"),
            link="",
        )
        entries = []
        for entry_node in _find_children(root, "entry"):
            link_node = _find_child(entry_node, "link")
            updated = _child_text(entry_node, "updated")
            content_nodes = []
            for content_node in _find_children(entry_node, "content"):
                content_nodes.append(
                    AttrDict(
                        type=content_node.attrib.get("type", ""),
                        value="".join(content_node.itertext()).strip(),
                    )
                )
            entries.append(
                SimpleNamespace(
                    title=_child_text(entry_node, "title"),
                    link=link_node.attrib.get("href", "") if link_node is not None else "",
                    updated=updated,
                    updated_parsed=_parsed_time(updated),
                    content=content_nodes,
                )
            )
        return SimpleNamespace(bozo=False, entries=entries, feed=feed)

    channel = _find_child(root, "channel")
    feed = SimpleNamespace(
        title=_child_text(channel, "title") if channel is not None else "",
        link=_child_text(channel, "link") if channel is not None else "",
    )
    entries = []
    for item_node in _find_children(channel, "item") if channel is not None else []:
        published = _child_text(item_node, "pubDate")
        media_thumbnail = [
            AttrDict(url=child.attrib.get("url", ""))
            for child in list(item_node)
            if _local_name(child.tag) == "thumbnail"
        ]
        enclosures = [
            AttrDict(
                href=child.attrib.get("url", ""),
                type=child.attrib.get("type", ""),
                title=child.attrib.get("title", ""),
            )
            for child in list(item_node)
            if _local_name(child.tag) == "enclosure"
        ]
        entries.append(
            SimpleNamespace(
                title=_child_text(item_node, "title"),
                link=_child_text(item_node, "link"),
                published=published,
                published_parsed=_parsed_time(published),
                summary=_child_text(item_node, "description"),
                description=_child_text(item_node, "description"),
                media_thumbnail=media_thumbnail,
                enclosures=enclosures,
                links=[],
            )
        )
    return SimpleNamespace(bozo=False, entries=entries, feed=feed)
