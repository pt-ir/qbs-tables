#!/usr/bin/python

import csv
import datetime
import itertools
import os
import re
import sys
import toposort
from lxml import etree

import geo

#the types to keep a copy of the raw version
_KEEP_RAW = ['placename', 'date', 'number', 'currency', 'rate']

# ----------------------------------------------------------------------

class CellType:
    """
    One type of data which we recognise; e.g. a date, a location,
    an integer.
    """

    @staticmethod
    def extent():
        """
        The name of the corresponding extent; need not be unique.  This
        is used for extents in the index
        """
        raise NotImplementedError

    @staticmethod
    def parents():
        """
        Return the next compatible "broader" classes, i.e. those whose
        string representations are a superset of this type's.  For
        example, float is the parent of int since all ways of writing
        an int value are also ways to write a float value.  Returns
        [] if there is no such type.
        """
        return []

    @classmethod
    def _append_path(cls, ret):
        for parent in cls.parents():
            if not (parent in ret):
                ret.append(parent)
        for parent in cls.parents():
            parent._append_path(ret)
        return ret

    @classmethod
    def get_path(cls):
        """
        Returns the path from this node to the ultimate parent type,
        in order bottom (self) to top (great^n grandparents).
        """
        ret = [ cls ]
        ret = cls._append_path(ret)
        return ret

    @staticmethod
    def unify_two(a, b):
        """
        Given a pair of types, find the best (narrowest, most defined)
        which accommodates both.  E.g., given [int, float] return float
        (ints are floats); given [int, datetime] return string (both are
        strings)
        """

        # sanity
        if (a == None) or (b == None):
            raise ValueError

        # easy cases
        if a == b:
            return a
        if (a == MissingType):
            return b
        if (b == MissingType):
            return a

        # okay, so we need to walk the tree.  For each one of a's parent
        # types, in order, see if it's also a parent of b's; if so, stop,
        # that's the one we want
        aparents = a.get_path()
        bparents = b.get_path()

        for ap in aparents:
            if ap in bparents:
                return ap

        # nope!  Give up.
        return None

    @staticmethod
    def unify_two_with_commentary(a, b):
        """
        As above, but be talkative
        """
        c = CellType.unify_two(a, b)
        return c

    @staticmethod
    def unify(ts):
        """
        Given a list of types, find the best (narrowest, most defined)
        which accommodates them all.  E.g., given [int, float, float]
        return float (ints are floats); given [int, datetime, bool] return
        string (all the above are strings)
        """
        if len(ts) == 0:
            return None
        elif len(ts) == 1:
            return ts[0]
        else:
            return reduce(CellType.unify_two_with_commentary, ts)

    @staticmethod
    def convert(s):
        """
        Given the value in a cell (string or None), convert a Python
        representation.
        """
        raise NotImplementedError

    @classmethod
    def to_index(cls, s):
        """
        Given the value in a cell (string or None), convert to the
        string we should put in the index
        """
        return str(cls.convert(s))

    @classmethod
    def to_term(cls, s):
        """
        Given the value in a cell (string or None), convert to the
        string we should use as a query term
        """
        return cls.to_index(s)

    @classmethod
    def to_query_op(cls, s):
        """
        Return an entire fragment of query, typically a #combine
        operator, for this string.
        """
        v = cls.to_term(s)
        return v + "." + cls.extent()
#        return "#combine[" + cls.extent() + "](" + v + ")"

class MissingType(CellType):

    """
    Missing data
    """

    @staticmethod
    def extent():
        return "missing"

    @staticmethod
    def parents():
        return []

    @staticmethod
    def convert(s):
        if s is None:
            return ''
        if s.upper() in ('', '\N', 'NA', 'N/A', 'UNK', 'N.A', 'N.A.'):
            return ''
        raise ValueError

    @classmethod
    def to_index(cls, s):
        return ''

    @classmethod
    def to_query_op(cls, s):
        return ''

class StringType(CellType):
    """
    Abritrary strings
    """

    @staticmethod
    def extent():
        return "string"

    @staticmethod
    def parents():
        return []

    @staticmethod
    def convert(s):
        return s

    @classmethod
    def to_query_op(cls, s):
        """
        Could be in the content, or could be a column header
        """
        v = cls.to_term(s)
        return "".join([
            "#or(",
            v + "." + cls.extent() + " ",
            v + ".header"
#            "#combine[" + cls.extent() + "](" + v + ") ",
#            "#combine[header](" + s + ")",
            ")"
        ])

class NumberType(CellType):
    """
    Numbers of some kind
    """
    @staticmethod
    def parents():
        return [ StringType ]

    @staticmethod
    def extent():
        return "number"

    @classmethod
    def to_query_op(cls, s):
        """
        Don't want the header re-writing of string values
        """
        v = cls.to_term(s)
        return v + "." + cls.extent()
#        return "#combine[" + cls.extent() + "](" + v + ")"

    SUFFIXES = {
        'B': 1000000000, 'M': 1000000, 'k': 1000
    }
    @staticmethod
    def convert(s):
        mult = 1
        s = s.translate(None, ", ")
        if len(s) > 1:
            try:
                mult = NumberType.SUFFIXES[s[-1]]
                s = s[0:-1]
            except KeyError:
                mult = 1
        try:
            return mult * int(s)
        except ValueError:
            return mult * float(s)

class RateType(NumberType):
    """
    Percentages and other rates
    """
    @staticmethod
    def extent():
        return "rate"

    @staticmethod
    def parents():
        return [ NumberType ]

    SUFFIXES = {
        '%': 1e-3, 'pc': 1e-3, 'percent': 1e-3, 'ppm': 1e-6
    }

    @staticmethod
    def convert(s):
        mult = 0
        s = s.translate(None, ", ")
        if len(s) > 1:
            for suff in RateType.SUFFIXES:
                l = len(suff)
                if s[-l:] == suff:
                    mult = RateType.SUFFIXES[suff]
                    s = s[:-l]
        if mult == 0:
            raise ValueError # we insist on a suffix
        try:
            return mult * int(s)
        except ValueError:
            return mult * float(s)  

class CurrencyType(NumberType):
    """
    Currencies -- just "$" for now.
    These are numbers so "$124" and "345" unify.
    """

    @staticmethod
    def extent():
        return "currency"

    @staticmethod
    def parents():
        return [ NumberType ]

    @staticmethod
    def convert(s):
        if len(s) < 2:
            raise ValueError
        if s[0] != '$':
            raise ValueError
        return NumberType.convert(s[1:])

class BoolType(CellType):
    """
    true/false, yes/no
    """

    @staticmethod
    def extent():
        return "bool"

    @staticmethod
    def parents():
        return [ StringType ]

    true_re = re.compile(r'^(t(rue)?|y(es)?)$', re.I)
    false_re = re.compile(r'^(f(alse)?|n(o)?)$', re.I)
    @staticmethod
    def convert(s):
        if BoolType.true_re.match(s):
            return True
        if BoolType.false_re.match(s):
            return False
        raise ValueError

class BoolFromIntType(CellType):
    """
    1/0
    """

    @staticmethod
    def extent():
        return "bool"

    @staticmethod
    def parents():
        return [ NumberType ]

    @staticmethod
    def convert(s):
        n = int(s)
        if n == 1:
            return True
        if n == 0:
            return False
        raise ValueError

class DateType(CellType):
    """
    Dates
    """

    @staticmethod
    def extent():
        return "date"

    @staticmethod
    def parents():
        return [ StringType ]

    FORMATS = [
        # '%Y' alone is covered by YearType below
        # year and month
        '%b %Y', '%Y %b', '%b-%Y', '%Y-%b',
        '%B %Y', '%Y %B', '%B-%Y', '%Y-%B',
        '%m/%Y', '%Y%m', '%Y-%m',  '%m-%Y',
        # year, month, day
        '%d %b %Y', '%Y %b %d', '%d %B %&', '%Y %B %d',
        '%d/%m/%Y', '%Y%m%d', '%Y-%m-%d'
        ]

# WTF dateutil, why do you parse "1" as "first of the current month"
# and "4.5" as "fourth of the current month"?  Stop trying so hard.

    @staticmethod
    def convert(s):
        for in_f in DateType.FORMATS:
            # try parsing
            # if it worked, format and spit back
            try:
                dt = datetime.datetime.strptime(s, in_f)
                out_f = '%Y'
                if ('%b' in in_f) or ('%B' in in_f) or ('%m' in in_f):
                    out_f = out_f + '_%m'
                    if ('%d' in in_f):
                        out_f = out_f + '_%d'
                return dt.strftime(out_f)
            except ValueError:
                pass
            pass
        raise ValueError

    @classmethod
    def to_term(cls, s):
        return cls.to_index(s) + "*"

class YearType(DateType):
    """
    A year alone
    """

    @staticmethod
    def parents():
        return [ DateType, NumberType ]

    @staticmethod
    def convert(s):
        y = int(s)
        if (y < 1700) or (y > 2100):
            raise ValueError
        else:
            return y

    @classmethod
    def to_index(cls, s):
        return str(int(s))

    @classmethod
    def to_term(cls, s):
        return cls.to_index(s) + "*"

class FYType(DateType):
    """
    A financial year
    """

    @staticmethod
    def parents():
        return [ DateType ]

    @staticmethod
    def convert(s):
        (a, b) = s.split('-')
        a = int(a)
        b = int(b)
        if (a < 1800) or (a > 2100):
            raise ValueError
        if b == a+1:
            return a
        if (b < 100) and (b == (a+1)%100):
            return a
        raise ValueError

    @classmethod
    def to_term(cls, s):
        return cls.to_index(s) + "*"

class YearRangeType(DateType):
    """
    A range of years
    """

    @staticmethod
    def parents():
        return [ DateType ]

    @staticmethod
    def convert(s):
        (a, b) = s.split('-')
        a = int(a)
        b = int(b)
        if (a < 1800) or (a > 2100):
            raise ValueError
        return (a, b)

    @classmethod
    def to_index(cls, s):
        try:
            (a, b) = cls.convert(s)
            return a + ' ' + b
        except:
            return s

    @classmethod
    def to_query_op(cls, s):
        (a, b) = cls.convert(s)
        ret = '#or( '
        for year in range(a, b+1):
            here = str(year) + '*.' + cls.extent() + ' '
            ret = ret + here
        ret = ret + ')'
        return ret

class DecadeType(DateType):
    """
    A whole decade, as in "1990s"
    """

    @staticmethod
    def parents():
        return [ NumberType ]

    @staticmethod
    def convert(s):
        if len(s) < 3:
            raise ValueError
        if s[-1] != 's':
            raise ValueError
        if s[-2] != '0':
            raise ValueError
        y = int(s[0:-1])
        return y

    @classmethod
    def to_term(cls, s):
        if len(s) < 3:
            raise ValueError
        if s[-1] != 's':
            raise ValueError
        if s[-2] != '0':
            raise ValueError
        start = int(s[0:-1])
        if start < 100:
            start = start + 1900
        ret = '#or( '
        for year in range(start, start+10):
            here = str(year) + '*.' + cls.extent() + ' '
            ret = ret + here
        ret = ret + ')'
        return ret
        # decade = int(s[0:-2])
        # return str(decade) + "*"

    @classmethod
    def to_query_op(cls, s):
        """
        Don't want the header re-writing of string values
        """
        return cls.to_term(s)

class GeoType(StringType):
    """
    A place
    """

    DIR = sys.path[0]
    AU = geo.GeoNames('Australia', 'AU',
                      open(os.path.join(DIR, 'au-geonames/admin1CodesASCII.txt')),
                      open(os.path.join(DIR, 'au-geonames/admin2Codes.txt')),
                      open(os.path.join(DIR, 'au-geonames/AU.txt')))

    @staticmethod
    def parents():
        return [ StringType ]

    @staticmethod
    def extent():
        return "placename"

    @staticmethod
    def convert(s):
        converted = GeoType.AU.extend(s)
        if converted is None:
            raise ValueError
        return converted

    @classmethod
    def to_term(cls, s):
        converted = cls.convert(s)
        return converted + "*"

# ----------------------------------------------------------------------

class Inferrer:
    """
    Guess the type(s) of text or of a table; rewrite them for indexing
    or querying.
    """

    # These are the types we know about.  In each case we have label,
    # checker function, and a "parent" type: "parent" types are less
    # restrictive.  For example, strings are parents of numbers since
    # "1" could be either.  The order of definition here doesn't
    # matter.
    known_types = [ StringType, NumberType, RateType, CurrencyType,
                    BoolType, BoolFromIntType,
                    DateType, YearType, YearRangeType, DecadeType,
                    GeoType ]

    # okay, now get a useful orderings: most- to least-restricted and
    # the opposite
    types_m2l = list(toposort.toposort_flatten(
        {t: set(t.parents()) for t in known_types}
    ))
    types_m2l.reverse()

    @staticmethod
    def infer_one_value(s):
        """
        Find the best (narrowest) data type for s, or None if there is no
        match (Should Never Happen(TM)).
        """
        # sanity
        if s is None:
            return MissingType

        s = s.strip()

        # missing values are okay, but special-cased
        try:
            MissingType.convert(s)
            return MissingType
        except ValueError:
            pass

        # we can just do the rest in order, since they're arranged most- to
        # least-restricted
        for t in Inferrer.types_m2l:
            try:
                converted = t.convert(s)
                return t
            except ValueError:
                pass
        return None

    @staticmethod
    def infer_one_column(col):
        """
        Look at the data in a column and return (header,type)
        * where header is the first row, if this looks different, or None
        * and type is one of the types above -- whichever is the narrowest
        (most restrictive) type which covers all the data.
        """

        # simple cases
        if len(col) == 0:
            return (None, None)

        if len(col) == 1:
            return (col[0], None)

        # now find the least restricted type which fits all the data,
        # we'll use that
        best_guesses = [Inferrer.infer_one_value(x) for x in col[1:]]
        best_type = CellType.unify(best_guesses)

        # okay, now we have an idea what the tightest possible type is
        # does the header match?
        header = col[0]
        header_type = Inferrer.infer_one_value(header)

        if header_type == None:
            # we have a blank cell, not a header
            header = None
        elif best_type == MissingType and header_type != MissingType:
            # we have a header for a blank column, keep it
            pass
        else:
            # would we change our mind about the type, if the first
            # cell were included?  if so, call it a header; if not, call
            # it data
            test_type = CellType.unify_two(header_type, best_type)
            if test_type == best_type:
                header = None # it's data

        return(header, best_type)

    @staticmethod
    def printable(t, v):
        """
        Utility: return a pretty version of value v, of type t
        """
        if v is None:
            return ""
        try:
            return t.to_index(v).strip()
        except ValueError:
            return str(v).strip()

    @staticmethod
    def rewrite_csv(fn):
        """
        Given the name of a file containing CSV data: read it, guess the
        types in each column, and print it out as quasi-XML which Indri
        can read and index.
        """

        with open(fn, 'rb') as csvfile:
            # guess the file type, check for headers
            sniffer = csv.Sniffer()
            buf = csvfile.read(1024)
            dialect = sniffer.sniff(buf, delimiters=",;")

            # check for a title: we have one if line 1 has a single cell
            # but line 2 is empty
            csvfile.seek(0)
            reader = csv.reader(csvfile, dialect)
            line1 = [ i for i in next(reader, []) if i != "" ]
            line2 = [ i for i in next(reader, []) if i != "" ]
            has_title = (len(line1) == 1) and (len(line2) == 0)
            if has_title:
                title = line1[0]
                start_line = 2
            else:
                start_line = 0

            # now read the CSV file; just take the first few lines, since
            # we are just guessing types at this stage
            csvfile.seek(0)
            header = itertools.islice(csvfile, start_line, 21 + start_line)
            reader = csv.reader(header, dialect)
            columns = itertools.izip_longest(*reader)
            has_header_row = False
            inferred_types = []

            for column in columns:
                (header, inferred_type) = Inferrer.infer_one_column(column)
                inferred_types.append(inferred_type)
                if header is not None:
                    has_header_row = True

            # now read the CSV file for real
            csvfile.seek(0)
            body = itertools.islice(csvfile, start_line, None)
            reader = csv.reader(body, dialect)
            columns = itertools.izip_longest(*reader)

            root = etree.Element('sheet')

            # and print it
            if has_title:
                h = etree.Element('header')
                h.text = title
                root.append(h)
            for (column_type, column) in zip(inferred_types, columns):
                coltype = column_type.extent()
                c = etree.Element('column', type=coltype)
                root.append(c)

                if has_header_row:
                    header = column[0]
                    if header is None:
                        header = ""
                    header = str(header).strip()
                    header_type = Inferrer.infer_one_value(header)
                    if header_type == None:
                        # we have a blank cell, not a header
                        header = ''
                    htext = Inferrer.printable(header_type, header)
                    htype = header_type.extent()
                    colhead = etree.Element('header', type=htype)
                    if htype in _KEEP_RAW:
                        colhead.attrib['raw'] = header.strip().decode('utf-8', 'ignore')
                    colhead.text = htext.decode('utf-8', 'ignore')
                    c.append(colhead)
                    column = column[1:]

                for val in column:
                    item = Inferrer.printable(column_type, val)
                    colitem = etree.Element('item')
                    if item:
                        if coltype in _KEEP_RAW:
                            colitem.attrib['raw'] = val.strip().decode('utf-8', 'ignore')
                        colitem.text = item.decode('utf-8', 'ignore')
                    c.append(colitem)
            print etree.tostring(root, pretty_print=True)

    @staticmethod
    def tokenise_query(line):
        """
        Given a whole query, split it into things that look like terms
        """
        line = re.sub(r'(\d{2,4})\s+-\s+(\d{2,4})', r'\g<1>-\g<2>', line)
        line = re.sub(r',', r'', line)
        return line.split(' ')

    @staticmethod
    def query_term(term):
        """
        Given a query term, turn it into (part of) an Indri query and
        return it
        """
        t = Inferrer.infer_one_value(term)
        return t.to_query_op(term)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="perform basic type inference for indexing and querying tables")
    parser.add_argument('files', nargs='*', help="the csv file(s) to rewrite into XML index files")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-q", "--query", action="store_true", help="rewrite a query (from stdin)")
    group.add_argument("-t", "--table", action="store_true", help="rewrite a named CSV table to XML for indexing")

    args = parser.parse_args()

    if args.query:
        for line in sys.stdin.readlines():
            for t in Inferrer.tokenise_query(line.strip()):
                print Inferrer.query_term(t)


    elif args.table:
        for line in sys.stdin.readlines():
            Inferrer.rewrite_csv(line.strip())

    elif len(args.files) > 0:
        for filename in args.files:
            Inferrer.rewrite_csv(filename)

    else:
        parser.print_help()
