# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests",
#     "tqdm",
# ]
# ///

import json
import time
import datetime
from pathlib import Path
import shutil
import csv
import bisect
import argparse

import requests
from tqdm import tqdm

ANKI_MEDIA = Path.home() / ".local/share/Anki2/User 1/collection.media"


class Parliament:
    def __init__(
        self,
        output_csv,
        input_csv=None,
        commons=True,
        lords=False,
        request_delay_ms=100,
        members=None,
    ):
        self.output_csv = output_csv
        self.input_csv = input_csv
        self.commons = commons
        self.lords = lords
        self.request_delay_ms = request_delay_ms
        if members:
            self.members = members
        else:
            self.members = []
            self.get_members()
        self.csv_rows = []
        self.get_portraits()
        self.read_csv()
        self.csv_rows += [m.to_csv() for m in self.members]
        self.write_csv()

    def get_members(self):
        members_json = []
        if self.commons:
            members_json += find_members_json(
                house=1, request_delay_ms=self.request_delay_ms
            )
        if self.lords:
            members_json += find_members_json(
                house=2, request_delay_ms=self.request_delay_ms
            )
        for item in tqdm(members_json, desc="Requesting biographies"):
            time.sleep(self.request_delay_ms / 1000)
            self.members.append(Member(item))

    def get_portraits(self):
        for member in tqdm(self.members, desc="Requesting portraits"):
            member.download_portrait()

    def read_csv(self):
        new_ids = sorted([m.member_id for m in self.members])
        rows = []
        with open(self.input_csv, newline="") as file:
            reader = csv.reader(file, delimiter="\t")
            for row in reader:
                try:
                    bisect_index(new_ids, int(row[0]))
                except ValueError:
                    self.csv_rows.append([row[0], ""] + row[2:])

    def write_csv(self):
        with open(self.output_csv, "w", newline="") as file:
            writer = csv.writer(file, delimiter="\t")
            for row in self.csv_rows:
                writer.writerow(row)


class Member:
    def __init__(self, member_data, biography=None, parliament=None):
        self.json = member_data
        self.parliament = parliament
        self.member_id = self.json["value"]["id"]
        self.name_list_as = self.json["value"]["nameListAs"]
        self.name_display_as = self.json["value"]["nameDisplayAs"]
        self.name_full_title = self.json["value"]["nameFullTitle"]
        self.gender = self.json["value"]["gender"]
        self.party = self.json["value"]["latestParty"]["name"]
        self.background_colour = self.json["value"]["latestParty"]["backgroundColour"]
        self.foreground_colour = self.json["value"]["latestParty"]["foregroundColour"]
        if biography:
            self.biography = biography
        else:
            self.biography = member_biography(self.member_id)
        representations = [
            r for r in self.biography["value"]["representations"] if not r["endDate"]
        ]
        if representations:
            self.current_representation = Representation(
                [r for r in representations if not r["endDate"]][0]
            )
        government_posts = self.biography["value"]["governmentPosts"]
        current_posts = [p for p in government_posts if not p["endDate"]]
        if not current_posts:
            self.government_post = None
        else:
            self.government_post = Post(current_posts[0])
        opposition_posts = self.biography["value"]["oppositionPosts"]
        current_posts = [p for p in opposition_posts if not p["endDate"]]
        if not current_posts:
            self.opposition_post = None
        else:
            self.opposition_post = Post(current_posts[0])
        house_memberships = self.biography["value"]["houseMemberships"]
        self.member_since = [
            read_date(m["startDate"]) for m in house_memberships if not m["endDate"]
        ][0]
        self.portrait_filename = f"parliament_member_{self.member_id}.jpg"
        self.portrait_filepath = ANKI_MEDIA / self.portrait_filename

    def download_portrait(self):
        if self.portrait_filepath.exists():
            return
        download_member_portrait(self.member_id, self.portrait_filepath)
        if self.parliament:
            time.sleep(self.parliament.request_delay_ms / 1000)

    def to_csv(self):
        if self.portrait_filepath.exists():
            portrait_html = f'<img src="{self.portrait_filename}">'
        else:
            portrait_html = None
        row = [
            self.member_id,
            True,
            self.name_list_as,
            self.name_display_as,
            self.name_full_title,
            self.gender,
            self.party,
            self.current_representation.name,
            constituency_url(self.current_representation.constituency_id),
            write_date(self.current_representation.start_date),
            self.background_colour,
            self.foreground_colour,
            write_date(self.member_since),
            portrait_html,
        ]
        if self.government_post:
            row += [
                self.government_post.name,
                write_date(self.government_post.start_date),
                self.government_post.additional_info,
                self.government_post.additional_info_link,
            ]
        else:
            row += [""] * 4
        if self.opposition_post:
            row += [
                self.opposition_post.name,
                write_date(self.opposition_post.start_date),
                self.opposition_post.additional_info,
                self.opposition_post.additional_info_link,
            ]
        else:
            row += [""] * 4
        row.append(write_date(datetime.date.today()))
        return row


class Representation:
    def __init__(self, data):
        self.json = data
        self.name = self.json["name"]
        self.constituency_id = self.json["id"]
        self.start_date = read_date(self.json["startDate"])
        self.end_date = (
            read_date(self.json["endDate"]) if self.json["endDate"] else None
        )


class Post:
    def __init__(self, data):
        self.json = data
        self.name = self.json["name"]
        self.start_date = read_date(self.json["startDate"])
        self.end_date = (
            read_date(self.json["endDate"]) if self.json["endDate"] else None
        )
        self.additional_info = self.json["additionalInfo"]
        self.additional_info_link = self.json["additionalInfoLink"]


def members_search(house, skip, take):
    headers = {
        "accept": "text/plain",
    }
    params = {
        "House": str(house),
        "IsCurrentMember": "true",
        "skip": str(skip),
        "take": str(take),
    }
    response = requests.get(
        "https://members-api.parliament.uk/api/Members/Search",
        params=params,
        headers=headers,
    ).json()
    return response


def member_biography(member_id):
    headers = {
        "accept": "text/plain",
    }
    response = requests.get(
        f"https://members-api.parliament.uk/api/Members/{member_id}/Biography",
        headers=headers,
    ).json()
    return response


def download_member_portrait(member_id, filename):
    params = {
        "cropType": "1",
        "webVersion": "true",
    }
    response = requests.get(
        f"https://members-api.parliament.uk/api/Members/{member_id}/Portrait",
        params=params,
        stream=True,
    )
    if response.status_code == 200:
        with open(filename, "wb") as file:
            response.raw.decode_content = True
            shutil.copyfileobj(response.raw, file)


def find_members_json(house=1, count=None, request_delay_ms=100):
    members = []
    take = min(count, 20) if count else 20
    response = members_search(house, 0, take)
    members += response["items"]
    total_results = int(response["totalResults"])
    limit = min(count, total_results) if count else total_results
    iterations = limit // 20
    for iteration in tqdm(
        range(iterations),
        desc="Requesting list of members",
    ):
        time.sleep(request_delay_ms / 1000)
        skip = 20 * (iteration + 1)
        response = members_search(house, skip, take)
        members += response["items"]
    print(f"{len(members)} members found.")
    return members


def read_date(date_string):
    return datetime.datetime.strptime(date_string, "%Y-%m-%dT%H:%M:%S").date()


def write_date(date):
    day = str(int(date.strftime("%d")))
    month_year = date.strftime("%B %Y")
    return f"{day} {month_year}"


def constituency_url(constituency_id):
    return f"https://members.parliament.uk/constituency/{constituency_id}/overview"


def bisect_index(sorted_list, item):
    """Return the index of `item` in `sorted_list` with a binary search."""
    index = bisect.bisect_left(sorted_list, item)
    if index != len(sorted_list) and sorted_list[index] == item:
        return index
    raise ValueError


def main():
    parser = argparse.ArgumentParser(
        description="Use the UK Parliament Members API to create an Anki deck."
    )
    parser.add_argument(
        "-o",
        "--output",
        default="output.csv",
        help="A CSV file, delimited by tabs (default=output.csv).",
    )
    parser.add_argument(
        "-i", "--input", help="A CSV file, delimited by tabs (default=None)."
    )
    parser.add_argument(
        "--commons",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Request members of the House of Commons (default=True).",
    )
    parser.add_argument(
        "--lords",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="Request members of the House of Lords (default=False).",
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=int,
        default=1000,
        help="The delay between HTTP requests (ms; default=1000).",
    )
    args = parser.parse_args()
    if not (args.commons or args.lords):
        return
    parliament = Parliament(
        args.output,
        input_csv=args.input,
        commons=args.commons,
        lords=args.lords,
        request_delay_ms=args.delay,
    )


if __name__ == "__main__":
    main()
