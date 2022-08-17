from email import message
import logging
from os import access
import re
import uuid
from flask import redirect, render_template, request, url_for, flash
from flask import current_app as app
from flask_login import login_required, current_user

from cryptoadvance.specter.specter import Specter
from cryptoadvance.specter.services.controller import user_secret_decrypted_required
from cryptoadvance.specter.user import User
from cryptoadvance.specter.wallet import Wallet
from .service import LoansService

from datetime import date, datetime
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

loans_endpoint = LoansService.blueprint

def ext() -> LoansService:
    ''' convenience for getting the extension-object'''
    return app.specter.ext["loans"]

def specter() -> Specter:
    ''' convenience for getting the specter-object'''
    return app.specter


def fetch_amount():
    data = LoansService.get_current_user_service_data()
    return {
        "btc_amount": data["btc_amount"],
        "ecash_amount": data["ecash_amount"],
    }

time_rate = {
    "3": 2,
    "6": 4,
    "9": 6,
    "12": 8,
}


@loans_endpoint.route("/", methods=["GET", "POST"])
@login_required
@user_secret_decrypted_required
def index():
    data = LoansService.get_current_user_service_data()
    if "btc_amount" not in data: # dummy wallet for proof of concept
        data["btc_amount"]=121.10
        data["ecash_amount"]=2300
        LoansService.update_current_user_service_data(data)

    if current_user == "admin":
        return render_template(
            "loans/lender/index.jinja",
            btc_amount=data["btc_amount"],
            ecash_amount=data["ecash_amount"],
        )
    else:
        username = str(current_user)
        common_data = LoansService.get_common_service_data()
        print(common_data)
        if "ecash_addresses" not in common_data:
            common_data["ecash_addresses"] = {}
            LoansService.update_common_service_data(common_data)
        if "incoming_requests" not in common_data:
            common_data["incoming_requests"] = []
            LoansService.update_common_service_data(common_data)

        addresses_list = [y[0] for y in filter(lambda x: username in x[1]["user"], common_data["ecash_addresses"].items())]

        print(request.method)
        if request.method == "POST":
            loan_req = {
                "id": uuid.uuid4().hex,
                "amount": float(request.form.get("amount", 0)),
                "rate": time_rate[request.form.get("rate", "")],
                "months": int(request.form.get("rate", 0)),
                "ecash_address": request.form.get("ecash_address", ""),
                "user": username,
                "status": "applied",
                "due": 0,
                "due_date": "",
            }
            btc_value = loan_req["amount"] * (100/20) / 1000 # 1 btc - 1000 ecash # actually giving 20%
            data["btc_amount"] = data["btc_amount"] - btc_value
            LoansService.update_current_user_service_data(data)
            common_data["incoming_requests"] += [loan_req]
            LoansService.update_common_service_data(common_data)
        

        return render_template(
            "loans/customer/index.jinja",
            btc_amount=data["btc_amount"],
            ecash_amount=data["ecash_amount"],
            addresses_list=addresses_list
        )


@loans_endpoint.route("/active_loans", methods=["GET", "POST"])
@login_required
@user_secret_decrypted_required
def active_loans():
    common_data = LoansService.get_common_service_data()
    username = str(current_user)
    print(common_data)
    data = LoansService.get_current_user_service_data()
    if "active_loans" not in common_data:
        common_data["active_loans"] = []
        LoansService.update_common_service_data(common_data)
    active_loans = common_data["active_loans"]
    print(active_loans)
    if current_user == "admin":
        return render_template(
            "loans/lender/active_loans.jinja",
            active_loans=active_loans
        )
    else:
        active_loans = list(filter(lambda x: x["user"] == username, active_loans))

        message = ""
 
        if request.method == "POST":
            button = request.form.get("button", "")
            if button == "pay":
                loan_id = request.form.get("id", "")
                loan = list(filter(lambda x: x["id"] == loan_id, common_data["active_loans"]))[0]
                index = common_data["active_loans"].index(loan)
                if loan["due"] == 0:
                    message = "Due is already paid for this month"
                elif loan["due"] <= data["ecash_amount"]:
                    data["ecash_amount"] -= loan["due"]
                    loan["due"] = 0
                    LoansService.update_current_user_service_data(data)

                    message = "Due paid successfully"

                    # check if loan is resolved
                    if loan["months"] == 1:
                        message += ". Loan cleared."
                        
                        # return back btc to user's wallet
                        if "deduct_btc" not in common_data:
                            common_data["deduct_btc"] = {}
                        if username not in common_data["deduct_btc"]:
                            common_data["deduct_btc"] = 0
                        common_data["deduct_btc"] += loan["btc_value"] # pending deduction from bank's wallet
                        common_data["active_loans"].pop(index)
                    else:
                        common_data["active_loans"][index] = loan

                    LoansService.update_common_service_data(common_data)
                else:
                    message = "You do not have enough ecash in wallet"

        return render_template(
            "loans/customer/active_loans.jinja",
            active_loans=active_loans,
            message=message
        )



@loans_endpoint.route("/pending_request", methods=["GET", "POST"])
@login_required
@user_secret_decrypted_required
def pending_request():
    username = str(current_user)
    common_data = LoansService.get_common_service_data()
    print(common_data)
    if "ecash_addresses" not in common_data:
        common_data["ecash_addresses"] = {}
        LoansService.update_common_service_data(common_data)
    if "incoming_requests" not in common_data:
        common_data["incoming_requests"] = []
        LoansService.update_common_service_data(common_data)
    if "active_loans" not in common_data:
        common_data["active_loans"] = []
        LoansService.update_common_service_data(common_data)
    print(common_data)

    if current_user == "admin":
        if request.method == "POST":
            loan_id = request.form.get("id", "")
            button = request.form.get("button", "")
            print(common_data["incoming_requests"])
            print(loan_id)
            print(list(filter(lambda x : x["id"] == loan_id, common_data["incoming_requests"])))
            loan_req = list(filter(lambda x : x["id"] == loan_id, common_data["incoming_requests"]))[0]
            # remove from incoming requests
            common_data["incoming_requests"].remove(loan_req)
            btc_value = loan_req["amount"] * (100/20) / 1000 # 1 btc - 1000 ecash # actually giving 20%
            if button == "accept":
                
                loan_req["btc_value"] = btc_value
                loan_req["status"] = "active"
                loan_req["due"] = (loan_req["amount"] * (100+loan_req["rate"])/100)/loan_req["months"]
                loan_req["due_date"] = str(date.today() + relativedelta(months=+1))

                # add to active loans
                common_data["active_loans"].append(loan_req)

                # update the bank's value
                data = LoansService.get_current_user_service_data()
                data["btc_amount"] += btc_value # bug - init
                data["ecash_amount"] -= loan_req["amount"]
                LoansService.update_current_user_service_data(data)

                # add this amount to user's wallet
                common_data["ecash_addresses"][loan_req["ecash_address"]]["balance"] += loan_req["amount"]
                print(common_data)
                LoansService.update_common_service_data(common_data)
            elif button == "decline":
                loan_req["status"] = "declined"

                #return back btc to user's wallet
                if "return_btc" not in common_data:
                    common_data["return_btc"] = {}
                if username not in common_data["return_btc"]:
                    common_data["return_btc"][loan_req["user"]] = 0
                common_data["return_btc"][loan_req["user"]] += btc_value

                if "inactive_loans" not in common_data:
                    common_data["inactive_loans"] = [] 
                common_data["inactive_loans"].append(loan_req)
                LoansService.update_common_service_data(common_data)

        return render_template(
            "loans/lender/pending_request.jinja",
            incoming_loans = common_data["incoming_requests"]
        )
    else:
        incoming_loans = list(filter(lambda x: x["user"] == username, common_data["incoming_requests"]))
        return render_template(
            "loans/customer/pending_request.jinja",
            incoming_loans = incoming_loans
        )


@loans_endpoint.route("/settings", methods=["GET", "POST"])
@login_required
@user_secret_decrypted_required
def settings():
    if current_user == "admin":
        return render_template(
            "loans/error.jinja",
        )
    else:
        message = ""
        username = str(current_user)
        if request.method == "POST":
            ecash_address = request.form.get("ecash_address", "")
            if len(ecash_address) != 10:
                message = "Incorrect address format"
            else:
                data = LoansService.get_common_service_data()
                if "ecash_addresses" not in data:
                    data["ecash_addresses"] = {}
                if ecash_address not in data["ecash_addresses"]:
                    data["ecash_addresses"][ecash_address] = {
                        "balance": 0,
                        "user": [username]
                    }
                    message = "Ecash address added successfully"
                else:
                    if username not in data["ecash_addresses"][ecash_address]["user"]:
                        data["ecash_addresses"][ecash_address]["user"] = data["ecash_addresses"][ecash_address]["user"] + [username]
                        message = "Ecash address added successfully"
                    else:
                        message = "Ecash address is already linked to your account"
                LoansService.update_common_service_data(data)

        return render_template(
            "loans/customer/settings.jinja",
            message=message,
        )

