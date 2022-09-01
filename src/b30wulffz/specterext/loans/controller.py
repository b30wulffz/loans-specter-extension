from email import message
import logging
from mimetypes import common_types
from os import access
import re
import uuid
from flask import redirect, render_template, request, url_for, flash
from flask import current_app as app
from flask_login import login_required, current_user
from flask_apscheduler import APScheduler
from functools import wraps

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

# month vs rate of the loan
time_rate = {
    "3": 2,
    "6": 4,
    "9": 6,
    "12": 8,
}

def ecash_to_loan_btc(ecash):
    loan_percentage = 0.2 
    btc_equi = 1000 # 1 btc = 1000 ecash
    return ecash / (loan_percentage * btc_equi) 

def offset_datetime_str(date):
    return (date + relativedelta(months=+1)).strftime("%d-%m-%Y %H:%M:%S")
    # return (date + relativedelta(seconds=+30)).strftime("%d-%m-%Y %H:%M:%S") # debug

def address_format_check(addr):
    return len(addr) != 10

def update_loan_due():
    now = datetime.now()
    common_data = LoansService.get_common_service_data()

    for ind in range(len(common_data["active_loans"])):
        loan = common_data["active_loans"][ind]

        due_date = datetime.strptime(loan["due_date"], "%d-%m-%Y %H:%M:%S")

        # assuming this algo runs atleast every month
        if now > due_date:
            # due_date = (due_date + relativedelta(months=+1)).strftime("%d-%m-%Y %H:%M:%S")
            loan["due"] = (loan["due"]*1.02) # 2% fine if not paid
            if(loan["months"] != 1):
                loan["due"] += loan["monthly_due"] # 2% fine if not paid
                loan["months"] -= 1
            loan["due_date"] = offset_datetime_str(due_date)
            # loan["due_date"] = (datetime.today() + relativedelta(seconds=+40)).strftime("%d-%m-%Y %H:%M:%S") #dev
            # loan["due_date"] = due_date
            common_data["active_loans"][ind] = loan

    LoansService.update_common_service_data(common_data)

scheduler = APScheduler()
# runs and updates loan due every 60 sec
scheduler.add_job("update_loan", update_loan_due, trigger='interval', seconds=60)
scheduler.start()

# checks if there are any pending transaction in the escrow
def escrow_facilitate_transaction():
    username = str(current_user)

    user_data = LoansService.get_current_user_service_data()
    common_data = LoansService.get_common_service_data()

    if username == "admin":
        # if "deduct_btc" not in common_data:
        #     common_data["deduct_btc"] = 0
        # assumes that bank as btc else, it will keep it negative, i.e. due
        user_data["btc_amount"] -= common_data["deduct_btc"]
        common_data["deduct_btc"] = 0

        # if "return_ecash" not in common_data:
        #     common_data["deduct_btc"] = 0
        user_data["ecash_amount"] += common_data["return_ecash"]
        common_data["return_ecash"] = 0
    else:
        # if "return_btc" not in common_data:
        #     common_data["return_btc"] = {}
        if username not in common_data["return_btc"]:
            common_data["return_btc"][username] = 0
        user_data["btc_amount"] += common_data["return_btc"][username] 
        common_data["return_btc"][username] = 0
    
    LoansService.update_common_service_data(common_data)
    LoansService.update_current_user_service_data(user_data)


def init(func):

    @wraps(func)
    def decorated_view(*args, **kwargs):
        # updating personal wallet
        data = LoansService.get_current_user_service_data()
        if "btc_amount" not in data: # dummy wallet for proof of concept
        # if True: #dev
            data["btc_amount"] = 121.10
            if current_user == "admin":
                data["ecash_amount"] = 2300000
            LoansService.update_current_user_service_data(data)

        # triggering dependency functions
        escrow_facilitate_transaction()
        return func(*args, **kwargs)

    return decorated_view

@loans_endpoint.route("/", methods=["GET", "POST"])
@login_required
@user_secret_decrypted_required
@init
def index():
    username = str(current_user)
    data = LoansService.get_current_user_service_data()
    message = ""

    if current_user == "admin":
        return render_template(
            "loans/lender/index.jinja",
            btc_amount=data["btc_amount"],
            ecash_amount=data["ecash_amount"],
        )
    else:
        common_data = LoansService.get_common_service_data()
        # print(common_data)
        # if "ecash_addresses" not in common_data:
        #     common_data["ecash_addresses"] = {}
        #     LoansService.update_common_service_data(common_data)
        # if "incoming_requests" not in common_data:
        #     common_data["incoming_requests"] = []
        #     LoansService.update_common_service_data(common_data)

        addresses_list = [y[0] for y in filter(lambda x: username in x[1]["user"], common_data["ecash_addresses"].items())]

        req_btc = -1
        default_amount = -1
        if request.method == "POST":
            btn = request.form.get("action", "")
            if btn == "get_loan":
                try:
                    if float(request.form.get("amount", 0)) <= 0:
                        message = "Invalid Amount"
                    elif request.form.get("rate", "") not in time_rate:
                        message = "Invalid Rate Input"
                    elif request.form.get("ecash_address", "") not in common_data["ecash_addresses"]:
                        message = "Invalid Ecash Address"
                    else:
                        loan_req = {
                            "id": uuid.uuid4().hex,
                            "amount": float(request.form.get("amount", 0)),
                            "rate": time_rate[request.form.get("rate", "")],
                            "months": int(request.form.get("rate", 0)),
                            "ecash_address": request.form.get("ecash_address", ""),
                            "user": username,
                            "status": "applied"
                        }
                        # btc_value = loan_req["amount"] * (100/20) / 1000 # 1 btc - 1000 ecash # actually giving 20%
                        btc_value = ecash_to_loan_btc(loan_req["amount"]) 
                        if btc_value > data["btc_amount"]:
                            message = "Not enough bitcoins found"
                        else:
                            data["btc_amount"] = data["btc_amount"] - btc_value
                            LoansService.update_current_user_service_data(data)
                            common_data["incoming_requests"] += [loan_req]
                            LoansService.update_common_service_data(common_data)
                            message = "Loan applied successfully"
                except:
                    message = "Invalid input"
            elif btn == "get_req_btc":
                try:
                    if float(request.form.get("amount", 0)) <= 0:
                        message = "Invalid Amount"
                    else:
                        default_amount = request.form.get("amount", 0)
                        amount = float(request.form.get("amount", 0))
                        req_btc = ecash_to_loan_btc(amount)
                except:
                    message = "Invalid input"
        return render_template(
            "loans/customer/index.jinja",
            btc_amount=data["btc_amount"],
            addresses_list=addresses_list, 
            message = message,
            default_amount = default_amount,
            req_btc = req_btc,
        )


@loans_endpoint.route("/active_loans", methods=["GET", "POST"])
@login_required
@user_secret_decrypted_required
@init
def active_loans():
    username = str(current_user)

    common_data = LoansService.get_common_service_data()
    # print(common_data)
    data = LoansService.get_current_user_service_data()
    # if "active_loans" not in common_data:
    #     common_data["active_loans"] = []
    #     LoansService.update_common_service_data(common_data)
    active_loans = common_data["active_loans"]
    # print(active_loans)
    if current_user == "admin":
        return render_template(
            "loans/lender/active_loans.jinja",
            active_loans=active_loans
        )
    else:
        active_loans = list(filter(lambda x: x["user"] == username, active_loans))

        message = ""

        addresses_list = [y[0] for y in filter(lambda x: username in x[1]["user"], common_data["ecash_addresses"].items())]
 
        if request.method == "POST":
            button = request.form.get("button", "")
            if button == "pay":
                loan_id = request.form.get("id", "")
                ecash_address = request.form.get("ecash_address", "")

                if ecash_address not in common_data["ecash_addresses"]:
                    message = "Invalid Ecash Address"
                else:
                    loan = list(filter(lambda x: x["id"] == loan_id, common_data["active_loans"]))[0]
                    index = common_data["active_loans"].index(loan)
                    if loan["due"] == 0:
                        message = "Due is already paid for this month"
                    elif loan["due"] <= common_data["ecash_addresses"][ecash_address]["balance"]:
                        common_data["ecash_addresses"][ecash_address]["balance"] -= loan["due"]
                        common_data["return_ecash"] += loan["due"]
                        loan["due"] = 0
                        LoansService.update_common_service_data(common_data)

                        message = "Due paid successfully"

                        # check if loan is resolved
                        if loan["months"] == 1:
                            message += ". Loan cleared."
                            
                            # return back btc to user's wallet
                            # if "deduct_btc" not in common_data:
                            #     common_data["deduct_btc"] = 0
                            data["btc_amount"] += loan["btc_value"]
                            common_data["deduct_btc"] += loan["btc_value"] # pending deduction from bank's wallet
                            common_data["active_loans"].pop(index)
                        else:
                            common_data["active_loans"][index] = loan

                        LoansService.update_common_service_data(common_data)
                        LoansService.update_current_user_service_data(data)
                    else:
                        message = "You do not have enough ecash in wallet"
        
        active_loans = list(filter(lambda x: x["user"] == username, common_data["active_loans"]))

        return render_template(
            "loans/customer/active_loans.jinja",
            active_loans=active_loans,
            message=message,
            addresses_list=addresses_list
        )



@loans_endpoint.route("/pending_request", methods=["GET", "POST"])
@login_required
@user_secret_decrypted_required
@init
def pending_request():
    username = str(current_user)

    common_data = LoansService.get_common_service_data()
    
    if current_user == "admin":
        if request.method == "POST":
            loan_id = request.form.get("id", "")
            button = request.form.get("button", "")
            loan_req = list(filter(lambda x : x["id"] == loan_id, common_data["incoming_requests"]))[0]
            # remove from incoming requests
            common_data["incoming_requests"].remove(loan_req)
            btc_value = ecash_to_loan_btc(loan_req["amount"])
            data = LoansService.get_current_user_service_data()
            if button == "accept" and loan_req["amount"] <= data["ecash_amount"]:
                
                loan_req["btc_value"] = btc_value
                loan_req["status"] = "active"
                loan_req["monthly_due"] = (loan_req["amount"] * (100+loan_req["rate"])/100)/loan_req["months"]
                loan_req["due"] = loan_req["monthly_due"]
                loan_req["due_date"] =  offset_datetime_str(datetime.today())

                # add to active loans
                common_data["active_loans"].append(loan_req)
                # print(loan_req)
                # update the bank's value
                data["btc_amount"] += btc_value # bug - init
                data["ecash_amount"] -= loan_req["amount"]
                LoansService.update_current_user_service_data(data)

                # add this amount to user's wallet
                common_data["ecash_addresses"][loan_req["ecash_address"]]["balance"] += loan_req["amount"]
                # print(common_data)
                LoansService.update_common_service_data(common_data)
            elif button == "decline":
                loan_req["status"] = "declined"

                if loan_req["amount"] > data["ecash_amount"]:
                    message = "Bank does not has enough eCash"
                
                #return back btc to user's wallet
                # if "return_btc" not in common_data:
                #     common_data["return_btc"] = {}
                if loan_req["user"] not in common_data["return_btc"]:
                    common_data["return_btc"][loan_req["user"]] = 0
                common_data["return_btc"][loan_req["user"]] += btc_value

                # if "inactive_loans" not in common_data:
                #     common_data["inactive_loans"] = [] 
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
@init
def settings():
    username = str(current_user)

    if current_user == "admin":
        return render_template(
            "loans/error.jinja",
        )
    else:
        message = ""
        if request.method == "POST":
            ecash_address = request.form.get("ecash_address", "")
            if address_format_check(ecash_address):
                message = "Incorrect address format"
            else:
                common_data = LoansService.get_common_service_data()
                # if "ecash_addresses" not in data:
                #     data["ecash_addresses"] = {}
                if ecash_address not in common_data["ecash_addresses"]:
                    common_data["ecash_addresses"][ecash_address] = {
                        "balance": 0,
                        "user": [username]
                    }
                    message = "Ecash address added successfully"
                else:
                    if username not in common_data["ecash_addresses"][ecash_address]["user"]:
                        common_data["ecash_addresses"][ecash_address]["user"] = common_data["ecash_addresses"][ecash_address]["user"] + [username]
                        message = "Ecash address added successfully"
                    else:
                        message = "Ecash address is already linked to your account"
                LoansService.update_common_service_data(common_data)

        return render_template(
            "loans/customer/settings.jinja",
            message=message,
        )

