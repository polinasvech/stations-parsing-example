import re
from datetime import datetime

from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta

from base import BaseSystem, Credential, InvalidCredentialsError, Point, Station, Transaction


class GasStationSystem(BaseSystem):
    base_url: str = "https://test-app.avtoversant.ru"

    @staticmethod
    def prepare_headers():
        return {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.129 Safari/537.36",
            "accept": "*/*",
            "x-requested-with": "XMLHttpRequest",
            "x-winter-request-handler": "onSignin",
        }

    def __init__(self) -> None:
        super().__init__()
        self.stations = dict()
        self.transactions = []
        self.headers = self.prepare_headers()

    def auth(self, credential: Credential) -> None:
        super().auth(credential)

        if self.credential.url:
            self.base_url = self.credential.url

        auth_url = f"{self.base_url}/account/login"
        payload = {
            "login": self.credential.login,
            "password": self.credential.password,
        }
        auth_response = self.connection.post(url=auth_url, json=payload, headers=self.headers)
        if auth_response.status_code != 200:
            raise InvalidCredentialsError("Invalid credentials")

    def get_stations(self):
        stations_url = f"{self.base_url}/abakam/gasstations/stations"
        response = self.connection.get(stations_url)

        for station in response.json():
            self.stations[station["name"]] = station

    def parse_transactions(self, table_data):
        table = BeautifulSoup(table_data, "html.parser")
        table = table.find("table")

        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")

            service = cells[5].text.strip()
            if service != "Пополнение баланса":
                station = None
                station_name = cells[4].text.strip()
                station_info = self.stations.get(station_name, None)
                if station_info:
                    point = Point(lat=station_info["lat"], lng=station_info["lng"])
                    station = Station(
                        code=station_info.get("id", None),
                        name=station_name,
                        brand=station_info.get("brand", None),
                        point=point,
                        address=station_info.get("address", None),
                    )

                transaction = Transaction(
                    credential=self.credential,
                    station=station,
                    card=cells[3].text.strip(),
                    code=cells[0].text.strip(),
                    date=datetime.strptime(cells[1].text.strip(), "%Y-%m-%d %H:%M:%S"),
                    service=cells[5].text.strip(),
                    sum=cells[-1].text.strip(),
                    volume=cells[-2].text.strip(),
                )

                self.transactions.append(transaction)

    def get_transactions(self, from_date: datetime, to_date: datetime) -> list[Transaction]:
        self.get_stations()

        transactions_url = f"{self.base_url}/account/transactions?page_size=100"
        transactions_url = transactions_url + "&page={}"

        contracts = [int(contract) for contract in self.credential.contracts.split(",")]

        for contract in contracts:
            payload = {
                "start_date": from_date.strftime("%Y-%m-%d"),
                "start_time": "00:00",
                "end_date": (to_date + relativedelta(days=1)).strftime("%Y-%m-%d"),
                "end_time": "00:00",
                "contract": contract,
            }
            self.headers["x-winter-request-handler"] = "onFilter"

            # parse the first page to find the total number of pages
            response = self.connection.post(url=transactions_url.format(1), json=payload, headers=self.headers)

            pagination_info = BeautifulSoup(response.json()["#data-pagination"], "html.parser")
            page_links = pagination_info.find_all("a", class_="page-link")

            max_page = 0
            for link in page_links:
                page_data = link.get("data-request-data", None)
                if page_data:
                    page_number = int(re.search(r"\d+", page_data).group()) if re.search(r"\d+", page_data) else 0
                    max_page = max(max_page, page_number)

            # parse transactions from first page
            self.parse_transactions(response.json()["#data-table"])
            # parse other pages
            for i in range(2, max_page + 1):
                response = self.connection.post(url=transactions_url.format(2), json=payload, headers=self.headers)
                self.parse_transactions(response.json()["#data-table"])

        self.connection.close()

        return self.transactions
