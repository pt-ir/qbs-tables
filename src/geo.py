# Simple geographic lookup tools, to support querying over typed data
# Paul Thomas, 2015

# Copyright (c) 2015 Commonwealth Scientific and Industrial Research
# Organisation (CSIRO)

import csv

class GeoNames:

    _country = ''
    _admin_1 = {}
    _admin_2 = {}
    _names = {}
    _alternates = {}

    def __init__(self, country_name, cc, admin_1_file, admin_2_file, country_file):
        """
        Set up for country code cc, reading data from the file object
        provided.
        """
        self._country = self._normalise(country_name)
        self.read_admin_1(admin_1_file, cc)
        self.read_admin_2(admin_2_file, cc)
        self.read_country(country_file)

    def read_admin_1(self, s, cc):
        """
        Read the top-level administrative codes from s, looking for
        those in country code cc.
        """
        for line in csv.reader(s, dialect="excel-tab"):
            (this_cc, this_id) = line[0].split('.')
            if this_cc == cc:
                self._admin_1[this_id] = self._normalise(line[2])

    def read_admin_2(self, s, cc):
        """
        Read the second-level administrative codes from s, looking for
        those in country code cc.
        """
        for line in csv.reader(s, dialect="excel-tab"):
            (this_cc, parent_id, this_id) = line[0].split('.')
            if this_cc == cc:
                self._admin_2[this_id] = self._normalise(line[2])

    def read_country(self, s):
        """
        Read a features file from s, which is anything file-like.
        Expects the XX.txt format from GeoNames, where XX is a country
        code.
        """
        for line in csv.reader(s, dialect="excel-tab"):
            if line[7][0:3] in ('PPL', 'ADM'):
                # canonical name
                name = self._normalise(line[2])
                a1 = line[10]
                a2 = line[11]
                self._names[name] = ( a1, a2 )
                # other names
                for alt in line[3].split(','):
                    self.add_alternate(alt, name)

    def add_alternate(self, alt, full):
        """
        Adds an alternate or abbreviation mapping 'alt' to 'full'.
        """
        # hard-code some unfortunate cases where an alternate is also
        # a common word; or empty
        alt = self._normalise(alt)
        if alt == "price" or alt == "":
            return

        self._alternates[alt] = self._normalise(full)

    def _normalise(self, name):
        """
        Case-fold, etc.
        """
        return name.strip().lower().translate(None, ",-. ")

    def _expand(self, name):
        """
        Expand any alternates.
        """
        try:
            return self._alternates[name]
        except KeyError:
            return name # no worries

    def exists(self, name):
        """
        Given a possible placename, return the normalised version iff
        we know about it; else None
        """
        name = self._expand(self._normalise(name))
        if name in self._names:
            return name
        elif name == self._country:
            return name
        else:
            return None

    def extend(self, name):
        """
        Given a possible placename, extend it to include all
        containing administrative areas: for example "Sydney" may be
        rewritten "australia_newsouthwales_sydney".  Returns None if
        it's not known.
        """
        name = self.exists(name)
        if name is None:
            return None
        if name == self._country:
            return name

        (a1, a2) = self._names[name]

        a2name = ''
        if a2 is not None and a2 != '':
            a2name = self._admin_2[a2]
            if a2name == name:
                a2name = ''
            elif a2name is not None and a2name != '':
                a2name = a2name + "_"

        a1name = ''
        if a1 is not None and a1 != '':
            a1name = self._admin_1[a1]
            if a1name == name:
                a1name = ''
            elif 'stateof' + a1name == name:
                # special case for e.g. 'stateofnewsouthwales'
                name = ''
            elif a1name is not None and a1name != '':
                a1name = a1name + "_"

        name = self._country + "_" + a1name + a2name + name
        return name

if __name__ == '__main__':
    a1 = open('au-geonames/admin1CodesASCII.txt')
    a2 = open('au-geonames/admin2Codes.txt')
    co = open('au-geonames/AU.txt')
    gn = GeoNames('Australia', 'AU', a1, a2, co)
    print gn.extend('murraybridge')
    print gn.extend('Murray Bridge')
    print gn.extend('NSW')
    print gn.extend('ACT')
    print gn.extend('Vic.')
    print gn.extend('Australia')
    print gn.extend('Wombat stew')
    print gn.extend('')
