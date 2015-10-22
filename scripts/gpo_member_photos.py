#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scrape http://memberguide.gpo.gov and
save members' photos named after their Bioguide IDs.
"""
from __future__ import print_function, unicode_literals
import argparse
import datetime
import os
import re
import sys
import json
import time
try:
    # Python 3
    from urllib.error import HTTPError
    from urllib.parse import parse_qs
    from urllib.parse import urlparse
except ImportError:
    # Python 2
    from urllib2 import HTTPError
    from urlparse import parse_qs
    from urlparse import urlparse

# pip install -r requirements.txt
import mechanicalsoup
import yaml


# Windows cmd.exe cannot do Unicode so encode first
def print_it(text):
    print(text.encode('utf-8'))


def pause(last, delay):
    if last is None:
        return datetime.datetime.now()

    now = datetime.datetime.now()
    delta = (now - last).total_seconds()

    if delta < delay:
        sleep = delay - delta
        print("Sleep for", int(sleep), "seconds")
        time.sleep(sleep)
    return datetime.datetime.now()


def get_front_page(br, congress_number, delay):

    print("Submit congress session number:", congress_number)

    # the JSON used to populate the memberguide site
    url = r'http://memberguide.gpo.gov/Congressional.svc/GetMembers/{0}'.format(congress_number)
    links = []

    ######################################
    # First, open the page to get the form
    ######################################
#     br.set_handle_robots(False)   # no robots
#     br.set_handle_refresh(False)  # can sometimes hang without this
    br.addheaders = [('User-agent',
                      'Mozilla/5.0 (X11; U; Linux i686; en-US; rv:1.9.0.1) '
                      'Gecko/2008071615 Fedora/3.0.1-1.fc9 Firefox/3.0.1')]

    response = br.get(url).text

    if len(response) == 0:
        sys.exit("Page is blank. Try again later, you may have hit a limit.")

    data = json.loads(response)
    for entry in data:
        link = entry["ImageUrl"]
        name = entry["LastName"] + ", " + entry["FirstName"]
        if congress_number in link:
            if ("DG" in link or
                "SR" in link or
                "RC" in link or
                "RP" in link):
                # Include only delegates, a resident commissioner,
                # representatives and senators.
                # Exclude capitol, house & senate officials ("CO", "HO", "SO"),
                # a president ("PR") and a vice-president ("VP") (8 in 113rd)
                links.append({"img_url": link, "name": name})
                # links will be a list of dictionaries where the "url" is a
                # link to the image and "name" is the rep's name

    print("Links:", len(links))
    return links


def load_yaml(filename):
    f = open(filename)
    data = yaml.safe_load(f)
    f.close()
    return data


def remove_from_yaml(data, bioguide_id):
    data[:] = [d for d in data if d['id']['bioguide'] != bioguide_id]
    return data


def get_value(item, key1, key2):
    value = None
    if key2 in item[key1].keys():
        value = item[key1][key2]
    return value


def resolve(data, text):
    if text is None:
        return None

    # hardcoded special cases to deal with bad data in GPO
    if text == "Bradley, Byrne":  # Really "Byrne, Bradley"
        return "B001289"
    elif text == "Curson, David Alan":  # Really "Curzon, David Alan"
        return "C001089"
    elif text == "Gutierrez, Luis":  # missing accent in lastname
        return "G000535"

    for item in data:
        bioguide = item['id']['bioguide']
        last = item['name']['last']
        first = item['name']['first']
        middle = get_value(item, 'name', 'middle')
        nickname = get_value(item, 'name', 'nickname')
        official = get_value(item, 'name', 'official_full')
        text_reversed = reverse_names(text)
        ballotpedia = get_value(item, 'id', 'ballotpedia')
        wikipedia = get_value(item, 'id', 'wikipedia')

        if text == last + ", " + first:
            return bioguide
        elif middle and text == last + ", " + first + " " + middle:
            return bioguide
        elif official and text_reversed == official:
            return bioguide
        elif nickname and text == last + ", " + nickname:
            return bioguide
        elif middle and text == last + ", " + first + " " + middle[0] + ".":
            return bioguide
        elif text.startswith(last) and ", " + first in text:
            return bioguide
        elif ballotpedia and ballotpedia == text_reversed:
            return bioguide
        elif wikipedia and wikipedia == text_reversed:
            return bioguide

        # Check all of first name, then all letters but last, ...,
        # then first letter
        for i in reversed(range(len(first))):
            if text.startswith(last) and ", " + first[:i+1] in text:
                return bioguide

    return None


def reverse_names(text):
    # Given names like "Hagan, Kay R.", reverse them to "Kay R. Hagan"
    return ' '.join(text.split(',')[::-1]).strip(" ")


# Make sure we have the congress-legislators repository available.
def download_legislator_data():
    # clone it if it's not out
    if not os.path.exists("congress-legislators"):
        print("Cloning the congress-legislators repo...")
        os.system("git clone -q --depth 1 "
                  "https://github.com/unitedstates/congress-legislators "
                  "congress-legislators")

    # Update the repo so we have the latest.
    print("Updating the congress-legislators repo...")
    # these two == git pull, but git pull ignores -q on the merge part
    # so is less quiet
    os.system("cd congress-legislators; git fetch -pq; "
              "git merge --ff-only -q origin/master")


def bioguide_id_from_url(url):
    bioguide_id = parse_qs(
        urlparse(url).query)['index'][0].strip("/")
    bioguide_id = str(bioguide_id.strip(u"\u200E"))
    bioguide_id = bioguide_id.capitalize()
    return bioguide_id


def bioguide_id_valid(bioguide_id):
    if not bioguide_id:
        return False

    # A letter then six digits
    # For example C001061

    # TODO: Is this specification correct?
    # Assume capital letter because ID finder will have uppercased it
    if re.match(r'[A-Z][0-9][0-9][0-9][0-9][0-9]', bioguide_id):
        return True

    return False


def save_metadata(bioguide_id):
    outdir = "congress/metadata"
    if not os.path.exists(outdir):
        os.makedirs(outdir)
    outfile = os.path.join(outdir, bioguide_id + ".yaml")
    with open(outfile, "w") as f:
        f.write("name: GPO Member Guide\n")
        f.write("link: http://memberguide.gpo.gov\n")


def download_photos(br, member_links, outdir, cachedir, delay):
    last_request_time = None
    print("Found a total of", len(member_links), "member links")
    if not os.path.exists(outdir):
        os.makedirs(outdir)
    if not os.path.exists(cachedir):
        os.makedirs(cachedir)

    todo_resolve = []
    legislators = load_yaml("congress-legislators/legislators-current.yaml")

    for i, member_link in enumerate(member_links):
        print("---")
        print("Processing member", i+1, "of", len(member_links), ":",
              member_link["name"].encode('latin-1'))
        bioguide_id = None

        cachefile = os.path.join(
            cachedir, member_link["img_url"].replace("/", "_") + ".html")
        # print(os.path.isfile(cachefile))

        html = ""

        if os.path.isfile(cachefile):
            # Load page from cache
            with open(cachefile, "r") as f:
                html = f.read()

        if len(html) == 0:
            # Open page with mechanize
            last_request_time = pause(last_request_time, delay)
            try:
                response = br.open(member_link["img_url"])
            except HTTPError:
                pass
            else:
                print(member_link["img_url"])
                # print(response.read())
                html = response.read()
                if len(html) > 0:
                    # Save page to cache
                    with open(cachefile, "w") as f:
                        f.write(html)

        # Resolve Bioguide ID against congress-legislators data
        # all IDs now have to be resolved, but names seem more consistent
        if not bioguide_id:
            bioguide_id = resolve(legislators, member_link['name'])

            if not bioguide_id:
                print("Bioguide ID not resolved")
                todo_resolve.append(member_link)

        # Download image
        if bioguide_id:
            print("Bioguide ID:", bioguide_id)

            # TODO: Fine for now as only one image on the page

            filename = os.path.join(outdir, bioguide_id + ".jpg")
            if os.path.isfile(filename):
                print("Image already exists:", filename)
            elif not args.test:
                print("Saving image to", filename)
                last_request_time = pause(last_request_time, delay)
                try:
                    data = br.open(member_link['img_url']).read()
                except HTTPError:
                    print("Image not available")
                else:
                    save = open(filename, 'wb')
                    save.write(data)
                    save.close()
                    save_metadata(bioguide_id)

        # Remove this from our YAML list to prevent any bad resolutions later
        legislators = remove_from_yaml(legislators, bioguide_id)

    # TODO: For each entry remaining here, check if they've since left
    # Congress. If not, either need to add a resolving case above, or fix the
    # GPO/YAML data.
    print("---")
    print("Didn't resolve Bioguide IDs:", len(todo_resolve))
    for member_link in todo_resolve:
        print(member_link['img_url'], member_link['name'])


def resize_photos():
    # Assumes they're congress/original/*.jpg
    os.system(os.path.join("scripts", "resize-photos.sh"))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape http://memberguide.gpo.gov and save "
                    "members' photos named after their Bioguide IDs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '-n', '--congress', default='114',
        help="Congress session number, for example: 110, 111, 112, 113")
    parser.add_argument(
        '-c', '--cache', default='cache',
        help="Directory to cache member pages")
    parser.add_argument(
        '-o', '--outdir', default="congress/original",
        help="Directory to save photos in")
    parser.add_argument(
        '-d', '--delay', type=int, default=5, metavar='seconds',
        help="Rate-limiting delay between scrape requests")
    parser.add_argument(
        '-1', '--one-page', action='store_true',
        help="Only process the first page of results (for testing)")
    parser.add_argument(
        '-t', '--test', action='store_true',
        help="Test mode: don't actually save images")
    args = parser.parse_args()

    # clone or update legislator YAML
    download_legislator_data()

    br = mechanicalsoup.Browser()
    member_links = get_front_page(br, args.congress, args.delay)

    download_photos(br, member_links, args.outdir, args.cache, args.delay)

    resize_photos()

# End of file
