#!/usr/bin/env python

import sys
import os.path

from datetime import datetime

import re
import json
import csv

from fnmatch import fnmatch

from lxml import etree
from dateutil import tz
import dateutil.parser
from copy import deepcopy

import lxml.html

json.encoder.c_make_encoder = None

try:
    from StringIO import StringIO
    from urllib2 import urlopen
    from ConfigParser import RawConfigParser
except ImportError:
    # python > 3
    from io import StringIO
    from urllib.request import urlopen
    from configparser import RawConfigParser

try:
    basestring
except NameError:
    basestring = unicode = str


def parse_rules(filename=None):
    if not filename:
        filename = os.path.join(os.path.dirname(__file__), 'feedify.ini')

    config = RawConfigParser()
    config.read(filename)

    rules = dict([(x, dict(config.items(x))) for x in config.sections()])

    for section in rules.keys():
        for arg in rules[section].keys():
            if '\n' in rules[section][arg]:
                rules[section][arg] = rules[section][arg].split('\n')[1:]

    return rules


def parse(data, url=None, mimetype=None):
    " Determine which ruleset to use "

    rulesets = parse_rules()
    parsers = [FeedXML, FeedHTML, FeedJSON]

    # 1) Look for a ruleset based on path

    if url is not None:
        for ruleset in rulesets.values():
            if 'path' in ruleset:
                for path in ruleset['path']:
                    if fnmatch(url, path):
                        parser = [x for x in parsers if x.mode == ruleset['mode']][0]
                        return parser(data, ruleset) 

    # 2) Look for a parser based on mimetype

    if mimetype is not None:
        parser_candidates = [x for x in parsers if mimetype in x.mimetype]

    if mimetype is None or parser_candidates is None:
        parser_candidates = parsers

    # 3) Look for working ruleset for given parser
        # 3a) See if parsing works
        # 3b) See if .items matches anything

    for parser in parser_candidates:
        ruleset_candidates = [x for x in rulesets.values() if x['mode'] == parser.mode and 'path' not in x]
            # 'path' as they should have been caught beforehands

        try:
            feed = parser(data)

        except (ValueError):
            # parsing did not work
            pass

        else:
            # parsing worked, now we try the rulesets

            for ruleset in ruleset_candidates:
                feed.rules = ruleset

                try:
                    feed.items[0]

                except (AttributeError, IndexError):
                    # parsing and or item picking did not work out
                    pass

                else:
                    # it worked!
                    return feed

    raise Exception('no way to handle this feed')


class ParserBase(object):
    def __init__(self, data=None, rules=None, parent=None):
        if rules is None:
            rules = parse_rules()[self.default_ruleset] # FIXME

        self.rules = rules

        if data is None:
            data = rules['base']

        self.root = self.parse(data)
        self.parent = parent


    def parse(self, raw):
        pass

    def remove(self):
        # delete oneslf
        pass

    def tostring(self):
        # output in its input format
        # to output in sth fancy (json, csv, html), change class type
        pass

    def torss(self):
        return self.convert(FeedXML).tostring()

    def tojson(self):
        return self.convert(FeedJSON).tostring()

    def tocsv(self):
        # TODO temporary
        out = StringIO()
        c = csv.writer(out, dialect=csv.excel)

        for item in self.items:
            row = [getattr(item, x) for x in item.dic]

            if sys.version_info[0] < 3:
                row = [x.encode('utf-8') if isinstance(x, unicode) else x for x in row]

            c.writerow(row)

        out.seek(0)
        return out.read()

    def tohtml(self):
        return self.convert(FeedHTML).tostring()

    def convert(self, TargetParser):
        target = TargetParser()

        for attr in target.dic:
            if attr == 'items':
                for item in self.items:
                    target.append(item)

            else:
                setattr(target, attr, getattr(self, attr))

        return target

    # RULE-BASED FUNCTIONS

    def rule_search(self, rule):
        # xpath, return the first one only
        try:
            return self.rule_search_all(rule)[0]

        except IndexError:
            return None

    def rule_search_all(self, rule):
        # xpath, return all raw matches (useful to find feed items)
        pass

    def rule_search_last(self, rule):
        # xpath, return only the first raw match
        try:
            return self.rule_search_all(rule)[-1]

        except IndexError:
            return None

    def rule_create(self, rule):
        # create node based on rule
        # (duplicate, copy existing (or template) or create from scratch, if possible)
        # --> might want to create node_duplicate helper fns
        pass

    def rule_remove(self, rule):
        # remove node from its parent
        pass

    def rule_set(self, rule, value):
        # value is always a str?
        pass

    def rule_str(self, rule):
        # GETs inside (pure) text from it
        pass

    # PARSERS

    def time_prs(self, x):
        # parse
        try:
            return parse_time(x)
        except ValueError:
            return None

    def time_fmt(self, x):
        # format
        try:
            time = parse_time(x)
            return time.strftime(self.rules['timeformat'])
        except ValueError:
            pass

    # HELPERS

    def get_raw(self, rule_name):
        # get the raw output, for self.get_raw('items')
        if rule_name not in self.rules:
            return []

        return self.rule_search_all(self.rules[rule_name])

    def get_str(self, rule_name):
        # simple function to get nice text from the rule name
        # for use in @property, ie. self.get_str('title')
        if rule_name not in self.rules:
            return None

        return self.rule_str(self.rules[rule_name])

    def set_str(self, rule_name, value):
        if rule_name not in self.rules:
            return None

        try:
            return self.rule_set(self.rules[rule_name], value)

        except AttributeError:
            # does not exist, have to create it
            self.rule_create(self.rules[rule_name])
            return self.rule_set(self.rules[rule_name], value)

    def rmv(self, rule_name):
        # easy deleter
        if rule_name not in self.rules:
            return

        self.rule_remove(self.rules[rule_name])


class ParserXML(ParserBase):
    default_ruleset = 'rss-channel'
    mode = 'xml'
    mimetype = ['text/xml', 'application/xml', 'application/rss+xml',
        'application/rdf+xml', 'application/atom+xml', 'application/xhtml+xml']

    NSMAP = {'atom': 'http://www.w3.org/2005/Atom',
        'atom03': 'http://purl.org/atom/ns#',
        'media': 'http://search.yahoo.com/mrss/',
        'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
        'slash': 'http://purl.org/rss/1.0/modules/slash/',
        'dc': 'http://purl.org/dc/elements/1.1/',
        'content': 'http://purl.org/rss/1.0/modules/content/',
        'rssfake': 'http://purl.org/rss/1.0/'}

    def parse(self, raw):
        parser = etree.XMLParser(recover=True)
        return etree.fromstring(raw, parser)

    def remove(self):
        return self.root.getparent().remove(self.root)

    def tostring(self, **k):
        return etree.tostring(self.root, **k)

    def _rule_parse(self, rule):
        test = re.search(r'^(.*)/@([a-z]+)$', rule) # to match //div/a/@href
        return test.groups() if test else (rule, None)

    def _resolve_ns(self, rule):
        # shortname to full name
        match = re.search(r'^([^:]+):([^:]+)$', rule) # to match fakerss:content
        if match:
            match = match.groups()
            if match[0] in self.NSMAP:
                return "{%s}%s" % (self.NSMAP[match[0]], match[1].lower())

        return rule

    @staticmethod
    def _inner_html(xml):
        return (xml.text or '') + ''.join([etree.tostring(child, encoding='unicode') for child in xml])

    @staticmethod
    def _clean_node(xml):
        if xml is not None and len(xml):
            [xml.remove(child) for child in xml]
            xml.text = None

    def rule_search_all(self, rule):
        try:
            return self.root.xpath(rule, namespaces=self.NSMAP)

        except etree.XPathEvalError:
            return []

    def rule_create(self, rule):
        # duplicate, copy from template or create from scratch
        rrule, key = self._rule_parse(rule)

        # try recreating based on the rule (for really basic rules, ie. plain RSS) `/feed/item`
        if re.search(r'^[a-zA-Z0-9/:]+$', rrule):
            chain = rrule.strip('/').split('/')
            current = self.root

            if rrule[0] == '/':
                # we skip the first chain-element, as we _start_ from the first/root one
                # i.e. for "/rss/channel/title" we only keep "/channel/title"
                chain = chain[1:]

            for (i, node) in enumerate(chain):
                test = current.find(self._resolve_ns(node))

                if test is not None and i < len(chain) - 1:
                    # yay, go on
                    current = test

                else:
                    # opps need to create
                    element = etree.Element(self._resolve_ns(node))
                    current.append(element)
                    current = element

            return current

        # try duplicating from existing (works well with fucked up structures)
        match = self.rule_search_last(rrule)
        if match:
            element = deepcopy(match)
            match.getparent().append(element)
            return element

        # try duplicating from template
        # FIXME
        # >>> self.xml.getroottree().getpath(ff.find('a'))

        return None

    def rule_remove(self, rule):
        rrule, key = self._rule_parse(rule)

        match = self.rule_search(rrule)

        if match is None:
            return

        elif key is not None:
            del x.attrib[key]

        else:
            match.getparent().remove(match)

    def rule_set(self, rule, value):
        rrule, key = self._rule_parse(rule)

        match = self.rule_search(rrule)

        html_rich = ('atom' in rule or self.rules['mode'] == 'html') \
            and rule in [self.rules.get('item_desc'), self.rules.get('item_content')]

        if key is not None:
            match.attrib[key] = value

        else:
            if html_rich:
                # atom stuff
                self._clean_node(match)
                match.attrib['type'] = 'xhtml'
                match.append(lxml.html.fragment_fromstring(value, create_parent='div'))

            else:
                if match is not None and len(match):
                    self._clean_node(match)
                    match.attrib['type'] = 'html'

                match.text = value

    def rule_str(self, rule):
        match = self.rule_search(rule)

        html_rich = ('atom' in rule or self.rules['mode'] == 'html') \
            and rule in [self.rules.get('item_desc'), self.rules.get('item_content')]

        if isinstance(match, etree._Element):
            if html_rich:
                # atom stuff
                return self._inner_html(match)

            else:
                return etree.tostring(match, method='text', encoding='unicode').strip()

        else:
            return match or ""


class ParserHTML(ParserXML):
    default_ruleset = 'html'
    mode = 'html'
    mimetype = ['text/html', 'application/xhtml+xml']

    def parse(self, raw):
        return lxml.html.fromstring(raw)

    def tostring(self, **k):
        return lxml.html.tostring(self.root, **k)

    def rule_search_all(self, rule):
        try:
            # do proper "class" matching (too "heavy" to type as-it in rules)
            pattern = r'\[class=([^\]]+)\]'
            repl = r'[@class and contains(concat(" ", normalize-space(@class), " "), " \1 ")]'
            rule = re.sub(pattern, repl, rule)

            return self.root.xpath(rule)

        except etree.XPathEvalError:
            return []

    def rule_create(self, rule):
        # try duplicating from existing (works well with fucked up structures)
        rrule, key = self._rule_parse(rule)

        match = self.rule_search_last(rule)
        if match is not None:
            element = deepcopy(match)
            match.getparent().append(element)

    # TODO def rule_set for the html part


def parse_time(value):
    if isinstance(value, basestring):
        if re.match(r'^[0-9]+$', value):
            return datetime.fromtimestamp(int(value), tz.tzutc())
        else:
            return dateutil.parser.parse(value, tzinfos=tz.tzutc)
    elif isinstance(value, int):
        return datetime.fromtimestamp(value, tz.tzutc())
    elif isinstance(value, datetime):
        return value
    else:
        return False


class ParserJSON(ParserBase):
    default_ruleset = 'json'
    mode = 'json'
    mimetype = ['application/json', 'application/javascript', 'text/javascript']

    def parse(self, raw):
        return json.loads(raw)

    def remove(self):
        # delete oneself FIXME
        pass

    def tostring(self):
        return json.dumps(self.root, indent=True, ensure_ascii=False)
            # ensure_ascii = False to have proper utf-8 string and not \u00

    def _rule_parse(self, rule):
        return rule.split(".")

    def rule_search_all(self, rule):
        try:
            rrule = self._rule_parse(rule)
            cur = self.root

            for node in rrule:
                if node == '[]':
                    break
                else:
                    cur = cur[node]

            return cur if isinstance(cur, list) else [cur,]

        except (AttributeError, KeyError):
            return []

    def rule_create(self, rule):
        # create from scracth
        rrule = self._rule_parse(rule)
        cur = self.root

        for (i, node) in enumerate(rrule):
            if rrule[i+1] == '[]':
                if node in cur and isinstance(cur[node], list):
                    cur[node].append({})

                else:
                    cur[node] = [{}]

                return

            else:
                if node in cur:
                    # yay, go on
                    cur = cur[node]
                else:
                    # opps need to create
                    cur[node] = {}

    def rule_remove(self, rule):
        if '[]' in rule:
            raise ValueError('not supported') # FIXME

        rrule = self._rule_parse(rule)
        cur = self.root

        for node in rrule[:-1]:
            cur = cur[node]

        del cur[rrule[-1]]

    def rule_set(self, rule, value):
        if '[]' in rule:
            raise ValueError('not supported') # FIXME

        rrule = self._rule_parse(rule)
        cur = self.root

        for node in rrule[:-1]:
            cur = cur[node]

        cur[rrule[-1]] = value

    def rule_str(self, rule):
        out = self.rule_search(rule)
        return str(out).replace('\n', '<br/>') if out else out


class Uniq(object):
    _map = {}
    _id = None

    def __new__(cls, *args, **kwargs):
        # check if a wrapper was already created for it
        # if so, reuse it
        # if not, create a new one
        # note that the item itself (the tree node) is created beforehands

        tmp_id = cls._gen_id(*args, **kwargs)
        if tmp_id in cls._map:
            return cls._map[tmp_id]

        else:
            obj = object.__new__(cls, *args, **kwargs)
            cls._map[tmp_id] = obj
            return obj


class Feed(object):
    itemsClass = 'Item'
    dic = ('title', 'desc', 'items')

    def wrap_items(self, items):
        itemsClass = globals()[self.itemsClass]
        return [itemsClass(x, self.rules, self) for x in items]

    title = property(
        lambda f:   f.get_str('title'),
        lambda f,x: f.set_str('title', x),
        lambda f:   f.rmv('title') )
    description = desc = property(
        lambda f:   f.get_str('desc'),
        lambda f,x: f.set_str('desc', x),
        lambda f:   f.rmv('desc') )
    items = property(
        lambda f:   f )

    def append(self, new=None):
        self.rule_create(self.rules['items'])
        item = self.items[-1]

        if new is None:
            return

        for attr in self.dic:
            try:
                setattr(item, attr, getattr(new, attr))

            except AttributeError:
                try:
                    setattr(item, attr, new[attr])

                except (IndexError, TypeError):
                    pass

    def __getitem__(self, key):
        return self.wrap_items(self.get_raw('items'))[key]

    def __delitem__(self, key):
        self[key].remove()

    def __len__(self):
        return len(self.get_raw('items'))


class Item(Uniq):
    dic = ('title', 'link', 'desc', 'content', 'time', 'updated')

    def __init__(self, xml=None, rules=None, parent=None):
        self._id = self._gen_id(xml)
        self.root = xml
        self.rules = rules
        self.parent = parent

    @staticmethod
    def _gen_id(xml=None, *args, **kwargs):
        return id(xml)

    title = property(
        lambda f:   f.get_str('item_title'),
        lambda f,x: f.set_str('item_title', x),
        lambda f:   f.rmv('item_title') )
    link = property(
        lambda f:   f.get_str('item_link'),
        lambda f,x: f.set_str('item_link', x),
        lambda f:   f.rmv('item_link') )
    description = desc = property(
        lambda f:   f.get_str('item_desc'),
        lambda f,x: f.set_str('item_desc', x),
        lambda f:   f.rmv('item_desc') )
    content = property(
        lambda f:   f.get_str('item_content'),
        lambda f,x: f.set_str('item_content', x),
        lambda f:   f.rmv('item_content') )
    time = property(
        lambda f:   f.time_fmt(f.get_str('item_time')),
        lambda f,x: f.set_str('title', f.time_prs(x)),
        lambda f:   f.rmv('item_time') )
    updated = property(
        lambda f:   f.time_fmt(f.get_str('item_updated')),
        lambda f,x: f.set_str('updated', f.time_prs(x)),
        lambda f:   f.rmv('item_updated') )


class FeedXML(Feed, ParserXML):
    itemsClass = 'ItemXML'

    def tostring(self, **k):
        # override needed due to "getroottree" inclusion
        return etree.tostring(self.root.getroottree(), **k)


class ItemXML(Item, ParserXML):
    pass


class FeedHTML(Feed, ParserHTML):
    itemsClass = 'ItemHTML'


class ItemHTML(Item, ParserHTML):
    pass


class FeedJSON(Feed, ParserJSON):
    itemsClass = 'ItemJSON'


class ItemJSON(Item, ParserJSON):
    def remove(self):
        rrule = self._rule_parse(self.rules['items'])
        cur = self.parent.root

        for node in rrule:
            if node == '[]':
                cur.remove(self.root)
                return

            cur = cur[node]
