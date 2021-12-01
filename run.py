#!flask/bin/python

from flask import Flask
from flask import abort
from flask import make_response
from flask import request
from flask import jsonify
from flask import render_template
from flask import Response
from flask import send_file

from flask_cors import CORS

from datetime import datetime

# Third-party libraries
from flask import Flask, redirect, request, url_for, flash
import os
import time
import sys
import csv
import configparser
import io
import redis

config = configparser.ConfigParser()
config.read('/etc/livedata/config.ini')
ledgerRoot = config['DEFAULT']['ledgersRoot'] if 'ledgersRoot' in config['DEFAULT'] else '/home/ec2-user/ledgers'
ledgerApiRoot = config['DEFAULT']['ledgersApiRoot'] if 'ledgersApiRoot' in config['DEFAULT'] else '/home/ec2-user/ledger-api'
# Ledger
sys.path.insert(0, ledgerRoot)
sys.path.insert(0, ledgerApiRoot)

from identityAuth import IdentityAuth
ledgerIdentityParser = IdentityAuth('ledger')
from ledger_application.ledger_app import LedgerApp as LedgerApp
from ledger_orchestration.generate_infile import import_batch_contacts_to_ledger
from ledger_python.batchImporter import BatchImporterThread, FlatfileBatchImporterThread
from ledger_python.fileSystem import FileSystem
from ledger_python.api_error import ApiError

import logging
import logging.config
import ledger_log.logging


ledger_app = LedgerApp()
ledger = ledger_app.get("ledger")
ledger_contact = ledger_app.get("contact")
batchImport = ledger_app.get("batchImport")

#TODO disable this in production
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)

app.config['CORS_HEADERS'] = 'Content-Type'
cors = CORS(app, resources={r"/api/*": {"origins": "*"}},
       headers={'Access-Control-Request-Headers', 'Content-Type', 'Access-Control-Allow-Origin'})
alt_checker = redis.Redis() #default connnection to local redis

@app.route('/api/ledger/v1')
def index():
    return "LiveData Ledger Service\n"


@app.errorhandler(404)
def not_found(error):
#     return render_template('maintenance.html')
    return make_response(jsonify({ 'error': 'Not found' }), 404)

# --------------------------------------
# Ledger API


########## MAINTENANCE ##########
def checkLedgerMaintenance():
    status = alt_checker.get("ledger_maintenance")
    if status is not None:
        status = status.decode('utf-8')
    if status == "on":
        abort(503, description="under maintenance")
    return True

@app.route('/maintenance')
def maintenance():
    abort(503, description="under maintenance")

@app.route('/api/ledger/v1/maintenance', methods=['GET'])
def getLedgerMaintenanceStatus():
    return jsonify({"ledgerMaintenance": alt_checker.get("ledger_maintenance").decode('utf-8')})

@app.route('/api/ledger/v1/maintenance', methods=['POST'])
def setLedgerMaintenance():
    (e_code, e_msg, req_user) = ledgerIdentityParser.check_employee_role('admin', request)
    if e_msg is not None:
        return jsonify({ "message": e_msg}), e_code
    required_params = [
        'maintenance'
    ]
    params = request.get_json()

    for p in required_params:
        if p not in params:
            print("Missing parameter: %s" % p)
            abort(400)

    maintenance = params.get('maintenance')
    if maintenance not in ['on', 'off']:
        print("wrong parameters")
        abort(400)

    alt_checker.set("ledger_maintenance", maintenance)  # set in local redis
    return jsonify({"message": f"ledger maintenance turned {maintenance}"}), 200

########## LEDGERS ##########
@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers', methods=['GET'])
def getLedgers(orgId):
    # TODO: implement jwt
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('viewer', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        params = {}
        params_list = ["name"]
        fields = []
        values = []

        params["name"]= request.args.get('name')
        sortBy = request.args.get('sortBy')  #   +name, -name
        limit = request.args.get('limit', 100)
        offset = request.args.get('offset')
        for param in params_list:
            if params.get(param) is not None:
                fields.append(param)
                values.append(params.get(param))
        ledgers = ledger.getLedgers(orgId, fields, values, sortBy, limit, offset)
        # ledgers = ledger.getLedgersByOrgId(orgId)
        return jsonify({"ledgers": ledgers})
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers', methods=['POST'])
def createLedger(orgId):
    checkLedgerMaintenance()
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('editor', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        params = request.get_json()
        if (params is None):
            raise ApiError("Missing input parameters", 400)

        verifyCompany = True if params.get("updateCompany") is True else False
        findEmail = True if params.get("updateEmail") is True else False
        findLinkedin = True if params.get("updateLinkedin") is True else False
        name = params.get("name", "")
        if len(name.strip()) == 0:
            return jsonify({ "message": "missing required fields"}), 422
        if len(name) > 256:
            return jsonify({ "message": "field too long"}), 422
        description = params.get("description")

        find_linkedin = findLinkedin
        find_company = verifyCompany
        verify_sameco = verifyCompany
        verify_newco = verifyCompany
        find_email = findEmail
        verify_email = findEmail
        find_phone = params.get("findPhone", False)
        find_directdial = params.get("findDirectCell", False)
        find_cell = params.get("findDirectCell", False)
        find_firmo = params.get("findFirmo", False)
        ledgerResult = ledger.createLedger(orgId, name, description, find_linkedin, find_company, find_email,
            find_phone, find_directdial, find_cell, find_firmo, verify_email, verify_sameco, verify_newco)
        return jsonify(ledger.ledgerJsonResult(orgId, ledgerResult))
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>', methods=['GET'])
def getLedger(orgId, ledgerId):
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('viewer', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        ledgerResult = ledger.getLedgerById(ledgerId)
        return jsonify(ledger.ledgerJsonResult(orgId, ledgerResult))
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>', methods=['PATCH'])
def updateLedger(orgId, ledgerId):
    checkLedgerMaintenance()
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('editor', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        params = request.get_json()
        if (params is None):
            raise ApiError("Missing input parameters", 400)
        verifyCompany = True if params.get("updateCompany") is True else False
        findEmail = True if params.get("updateEmail") is True else False
        findLinkedin = True if params.get("updateLinkedin") is True else False
        name = params.get("name")
        description = params.get("description")

        find_linkedin = findLinkedin
        find_company = verifyCompany
        verify_sameco = verifyCompany
        verify_newco = verifyCompany
        find_email = findEmail
        verify_email = findEmail
        find_phone = params.get("findPhone", False)
        find_directdial = params.get("findDirectCell", False)
        find_cell = params.get("findDirectCell", False)
        find_firmo = params.get("findFirmo", False)
        ledgerResult = ledger.updateLedger(ledgerId, name, description, find_linkedin, find_company, find_email,
            find_phone, find_directdial, find_cell, find_firmo, verify_email, verify_sameco, verify_newco)
        return jsonify(ledger.ledgerJsonResult(orgId, ledgerResult))
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>', methods=['DELETE'])
def deleteLedger(orgId, ledgerId):
    checkLedgerMaintenance()
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('editor', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        params = request.get_json()
        if ledger.deleteLedger(ledgerId):
            return jsonify({"success": True})
        else:
            return jsonify({"success": False})
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

########## LEDGER IMPORTS ##########

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts/imports/flatfile', methods=['POST'])
def submitFlatfileImport(orgId, ledgerId):
    checkLedgerMaintenance()
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('editor', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        params = request.get_json()
        flatfile_batchId = params.get("batchId")
        fileName = params.get("fileName")
        manual = params.get("manual")
        if manual:
            importType = "manualInput"
        else:
            importType = "fileUpload"
        contactsNum = params.get("contactsNum")
        originalRows = params.get("originalRows")
        flatfile_info = {}
        flatfile_info["ff_guid"] = flatfile_batchId
        flatfile_info["ff_count_input"] = params.get("originalRows")
        flatfile_info["count_submitted"] = params.get("contactsNum")
        importedBy = req_user.get("id")
        batch_import = batchImport.createBatchImportFrom(ledgerId, importedBy, fileName, importType, flatfile_info)
        thread = FlatfileBatchImporterThread(ledger_app, batch_import.ledger_import_id, flatfile_batchId)
        thread.start()

        return jsonify({
            "guid": batch_import.ledger_import_id,
            "fileName": batch_import.filename,
            "contactsNum": contactsNum,
            "originalRows": originalRows,
            "status": "importing",
            "importType": batch_import.import_type,
            "importStartedAt": datetime.utcnow(),
            "importFinishedAt": None,
            "errorsCsvHref": None,
            "type": "ledger#import"
        })
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts/imports', methods=['GET'])
def getImportBatchesForLedger(orgId, ledgerId):
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('viewer', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        params = {}
        params_list = ["fileName", "status", "importType"]
        fields = []
        values = []

        params["fileName"]= request.args.get('fileName')
        params["status"]= request.args.get('status')
        params["importType"]= request.args.get('importType')
        sortBy = request.args.get('sortBy')  #   +name, -name
        limit = request.args.get('limit', 100)
        offset = request.args.get('offset')
        for param in params_list:
            if params.get(param) is not None:
                fields.append(param)
                values.append(params.get(param))
        batch_imports = batchImport.getLedgerImports(ledgerId, fields, values, sortBy, limit, offset)
        importsFinalList = []
        if batch_imports is not None:
            for batch in batch_imports:
                importsFinalList.append(batchImport.batchImportJsonResult(orgId, batch))
        return jsonify({
            "imports": importsFinalList
        })
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts/imports/<string:importId>', methods=['GET'])
def getImportBatchesForId(orgId, ledgerId, importId):
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('viewer', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        batch_import = batchImport.getBatchImportFromId(importId)
        return jsonify(batchImport.batchImportJsonResult(orgId, batch_import))
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts/imports/<string:importId>/downloadErrors', methods=['GET'])
def downloadBatchImportErrorsCsv(orgId, ledgerId, importId):
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('viewer', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        path = "ledgers/errors/ledger_batch_import_%s_errors.csv" % importId
        fs = FileSystem('s3')
        file = fs.open(path, 'rb')
        returnfileContents = file.read().decode("utf-8")

        batch_import = batchImport.getBatchImportFromId(importId)
        originalFileName = batch_import.filename
        returnFileName = f"{originalFileName}_ledger_import_errors.csv"
        return Response(returnfileContents,
            mimetype="text/csv",
            headers={"Content-disposition":
                     f"attachment; filename={returnFileName}"})
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

########## CONTACTS ##########

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts', methods=['GET'])
def getLedgerContacts(orgId, ledgerId):
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('viewer', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        params = {}
        params_list = ["name", "company", "title", "fromDate", "endDate", "isVerified", "importCompany", "importTitle", "referenceId", "linkedin"]
        fields = []
        values = []

        params["name"]= request.args.get('name')
        params["company"] = request.args.get('company')
        params["domain"] = request.args.get('domain')
        params["title"] = request.args.get('title')
        params["page"] = request.args.get('page')
        params["fromDate"] = request.args.get('fromDate')
        params["endDate"] = request.args.get('endDate')
        params["isVerified"] = request.args.get('isVerified')
        params["importCompany"] = request.args.get('importCompany')
        params["importTitle"] = request.args.get('importTitle')
        params["referenceId"] = request.args.get('referenceId')
        params["linkedin"] = request.args.get('linkedin')
        view = request.args.get('view', 'default')
        sortBy = request.args.get('sortBy')  #   +name, -name
        limit = request.args.get('limit', 100)
        offset = request.args.get('offset')

        for param in params_list:
            if params.get(param) is not None:
                fields.append(param)
                values.append(params.get(param))

        contacts_results = ledger_contact.findContacts(ledgerId, fields, values, sortBy, limit, offset, view)
        results = {"type": "ledger#contact"}
        if view == "default":
            results["contacts"] = contacts_results
        elif view == "count":
            results["count"] = contacts_results
        return jsonify(results)
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts/download', methods=['GET'])
def downloadLedgerContacts(orgId, ledgerId):
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('viewer', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        params = {}
        params_list = ["name", "company", "title", "fromDate", "endDate", "isVerified", "importCompany", "importTitle", "referenceId", "linkedin"]
        fields = []
        values = []

        params["name"]= request.args.get('name')
        params["company"] = request.args.get('company')
        params["domain"] = request.args.get('domain')
        params["title"] = request.args.get('title')
        params["fromDate"] = request.args.get('fromDate')
        params["endDate"] = request.args.get('endDate')
        params["isVerified"] = request.args.get('isVerified')
        params["importCompany"] = request.args.get('importCompany')
        params["importTitle"] = request.args.get('importTitle')
        params["referenceId"] = request.args.get('referenceId')
        params["linkedin"] = request.args.get('linkedin')
        sortBy = request.args.get('sortBy')  #   +name, -name
        limit = request.args.get('limit')
        if limit is None:
            limit = 10000000
        offset = request.args.get('offset')

        for param in params_list:
            if params.get(param) is not None:
                fields.append(param)
                values.append(params.get(param))

        contacts = ledger_contact.findContacts(ledgerId, fields, values, sortBy, limit, offset, "default")
        returnfileContents = io.StringIO()
        fieldnames = ["id", "referenceId", "name", "linkedin", "importCompany", "importTitle", "company", "title",  "createdAt", "lastChangedAt", "isVerified"]
        writer = csv.DictWriter(returnfileContents, fieldnames=fieldnames, extrasaction='ignore')
        writer.writer.writerow(["id", "Reference id", "Name", "Linkedin","Imported Company", "Imported Title", "Current Company", "Current Title",  "Created At", "Last Changed At", "Verified"])
        for contact in contacts:
            writer.writerow(contact)
        timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        returnFileName = f"livedata-ledger-{ledgerId}-{timestamp}.csv"
        return Response(returnfileContents.getvalue(),
            mimetype="text/csv",
            headers={"Content-disposition":
                     f"attachment; filename={returnFileName}"})
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts', methods=['POST'])
def createLedgerContactsFromJson(orgId, ledgerId):
    checkLedgerMaintenance()
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('editor', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        params = request.get_json()
        if (params is None):
            raise ApiError("Missing input parameters", 400)
        contacts = params.get("contacts", None)
        if contacts is None:
            raise ApiError("Missing input parameters", 400)
        importedBy = req_user.get("id")
        batch_import = batchImport.createBatchImportFrom(ledgerId, importedBy, None, "apiImport")
        count_imported = 0
        count_duplicates = 0
        count_errors = 0
        count_submitted = len(contacts)
        error_records = []
        for contact in contacts:
            name = contact.get("name")
            title = contact.get("title")
            company = contact.get("company")
            lipid = contact.get("linkedin")
            referenceId = contact.get("referenceId")
            (imported, error) = ledger_contact.importContact(batch_import.ledger_import_id, name, company, lipid, title, referenceId)
            if imported is True:
                count_imported += 1
            if error is not None:
                if error == "DUPLICATE RECORD":
                    count_duplicates += 1
                else:
                    count_errors += 1
                contact["error"] = error
                error_records.append(contact)
        if len(error_records) > 0:
            destination = "ledgers/errors/ledger_batch_import_%s_errors.csv" % batch_import.ledger_import_id
            fs = FileSystem('s3')
            with fs.open(destination, 'w') as writeFile:
                errorWriter = csv.writer(writeFile)
                errorHeaderRow = ['Name', 'Title', 'Company', 'Linkedin', 'Error']
                errorWriter.writerow(errorHeaderRow)
                for contact in error_records:
                    errorWriter.writerow([contact.get('name'), contact.get('title'), contact.get('compant'), contact.get('linkedin'), contact.get('error')])
        date_finished = datetime.utcnow()
        batchImport.updateBatchImport(batch_import.ledger_import_id,
            {
                "status": "complete",
                "date_finished": date_finished,
                "count_duplicates": count_duplicates,
                "count_failures": count_errors,
                "count_submitted": count_submitted,
                "count_imported": count_imported
            }
        )
        return jsonify({
            "type": "ledger#batchImport",
            "guid": batch_import.ledger_import_id,
            "contactsNum": len(contacts),
            "importStartedAt": batch_import.date_created,
            "importFinishedAt": date_finished,
            "importType": batch_import.import_type,
            "count_duplicates": count_duplicates,
            "count_failures": count_errors,
            "status": "complete"
        })
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts/duplicates', methods=['GET'])
def getLedgerDuplicateContacts(orgId, ledgerId):
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('viewer', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        params = {}
        params_list = ["name", "company", "title", "fromDate", "endDate", "isVerified", "importCompany", "importTitle", "referenceId", "linkedin"]
        fields = []
        values = []

        params["name"]= request.args.get('name')
        params["company"] = request.args.get('company')
        params["domain"] = request.args.get('domain')
        params["title"] = request.args.get('title')
        params["page"] = request.args.get('page')
        params["fromDate"] = request.args.get('fromDate')
        params["endDate"] = request.args.get('endDate')
        params["isVerified"] = request.args.get('isVerified')
        params["importCompany"] = request.args.get('importCompany')
        params["importTitle"] = request.args.get('importTitle')
        params["referenceId"] = request.args.get('referenceId')
        params["linkedin"] = request.args.get('linkedin')

        sortBy = request.args.get('sortBy')  #   +name, -name
        limit = request.args.get('limit', 100)
        offset = request.args.get('offset')

        for param in params_list:
            if params.get(param) is not None:
                fields.append(param)
                values.append(params.get(param))

        contacts = ledger_contact.findContacts(ledgerId, fields, values, sortBy, limit, offset, "duplicates")
        return jsonify({
            "type": "ledger#contact",
            "contacts": contacts
        })
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts/bulk', methods=['POST'])
def uploadBulkLedgerContacts(orgId, ledgerId):
    checkLedgerMaintenance()
    try:
        fs = FileSystem('s3')
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('editor', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        file = request.files['file']
        if not file:
            raise ApiError("Missing file", 400)

        if (not file.filename.endswith(".csv")):
            raise ApiError("Unsupported file type", 400)

        folderName = account.folder if account.folder is not None else account.api_key
        fileName = file.filename
        filename_parsed = fileName.replace("'","")
        source = "%s/input/%s" % (folderName, filename_parsed)
        fs.uploadFileHandleToS3(file, source)
        importedBy = req_user.get("id")
        batch_import = batchImport.createBatchImportFrom(ledgerId, importedBy, fileName, "apiFileUpload")

        destination = "%s/intermediate/search/input/%s.csv" % (folderName, batch_import.ledger_import_id)
        fs.copy(source, destination)
        destination = "%s/input/%s.csv" % (folderName, batch_import.ledger_import_id)
        fs.copy(source, destination)

        headers = fileProcessor.getHeaders(destination)
        file_preview_raw = fileProcessor.previewFile(destination)
        return jsonify({
            "type": "ledger#batchImport",
            "id": batch_import.ledger_import_id,
            "columnHeaders": headers,
            "rows": file_preview_raw
        })
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts/imports/<string:importId>', methods=['POST'])
def submitMappingAndImportContacts(orgId, ledgerId, importId):
    checkLedgerMaintenance()
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('editor', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        website_to_backend_name_map = {
        # "Unique": "unique",
        "First Name": "first_name",
        "Last Name": "last_name",
        "Full Name": "full_name",
        "Company": "company",
        "Title": "title",
        "Email": "email",
        "LinkedIn": "linkedin",
        "Domain": "domain",
        "City": "city",
        "SIC": "sic"
        }

        folder = orgId
        path = batchImport.getSourcePathForImport(importId, folder)
        headers = fileProcessor.getHeaders(path)
        params = {}
        website_mapping = request.get_json()
        fullNameColumnExists = False
        firstNameColumnExists = False
        lastNameColumnExists = False
        companyColumnExists = False
        for key_pair in website_mapping["columns"]:
            potential_column_match = key_pair["clientColumn"]
            if headers:
                for idx, header in enumerate(headers):
                    if (header == potential_column_match):
                        params[website_to_backend_name_map[key_pair["cleanColumn"]]] = idx
                        if key_pair["cleanColumn"] == "Company":
                            companyColumnExists = True
                        if key_pair["cleanColumn"] == "Full Name":
                            fullNameColumnExists = True
                        if key_pair["cleanColumn"] == "First Name":
                            firstNameColumnExists = True
                        if key_pair["cleanColumn"] == "Last Name":
                            lastNameColumnExists = True
                        break
        if companyColumnExists and (fullNameColumnExists or (firstNameColumnExists and lastNameColumnExists)):
            batchImport.createBatchImportMapping(importId, params)
            thread = BatchImporterThread(ledger_app, importId)
            thread.start()
            return jsonify({
                "status": "importing",
                "type": "ledger#batchImport",
                "id": importId
            })
        else:
            return jsonify({"message": "missing required fields"}), 422
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts/<string:contactId>', methods=['DELETE'])
def deleteContact(orgId, ledgerId, contactId):
    checkLedgerMaintenance()
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('editor', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        ledger_contact.deleteContactById(contactId)
        return jsonify({"success": True})
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

@app.route('/api/ledger/v1/orgs/<string:orgId>/ledgers/<string:ledgerId>/contacts/bulk_delete', methods=['POST'])
def deleteBulkContact(orgId, ledgerId):
    checkLedgerMaintenance()
    try:
        (u_code, u_msg, req_user) = ledgerIdentityParser.check_user_role('editor', orgId, request)
        if u_msg is not None:
            return jsonify({ "message": u_msg}), u_code
        params = request.get_json()
        contact_ids = params.get("contactIds")
        if contact_ids is None:
            return jsonify({ "message": "missing required fields"}), 422
        if len(contact_ids) == 0:
            return jsonify({ "message": "missing required fields"}), 422
        count_deleted = ledger_contact.deleteBulkContactByIds(ledgerId, contact_ids)
        if count_deleted is None:
            return jsonify({"countRowsDeleted": 0})
        else:
            return jsonify({"countRowsDeleted": count_deleted})
    except ApiError as e:
        return jsonify({ "message": e.message}), e.code

# --------------------------------------


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000,debug=True, ssl_context="adhoc")
    #app.run(debug=True, ssl_context="adhoc")
