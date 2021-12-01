import redis
import requests
import jwt
import logging
from flask import request

import configparser
config = configparser.ConfigParser()
config.read('/etc/livedata/config.ini')

class IdentityAuth:

    def __init__(self, service):
        self.key = bytes(config['Identity']['secret'], 'utf-8')
        self.service = service

    def decode(self, token):
        return jwt.decode(token, self.key, algorithms=["HS256"])

    def user_role(self, org_id, decoded):
        try:
            roles = decoded['roles']
            for role in roles:
                (t_org, level) = role.split(":")
                if t_org == "*": #employee, can work across all roles
                    return level
                if t_org == org_id:
                    return level
        except Exception as e:
            logging.exception(e)
            return None
        return None

    def employee_role(self, service, decoded):
        try:
            i_roles = decoded['internal_roles']
            for i_role in i_roles:
                (t_service, level) = i_role.split(":")
                if t_service == service:
                    return level
        except Exception as e:
            logging.exception(e)
            return None
        return None

    def user_id_matches(self, user_id, decoded):
        try:
            uid = decoded['sub']
            logging.info(f"comparing {user_id} to {uid}")
            if uid == user_id:
                return True
        except Exception as e:
            logging.exception(e)
            return False
        return False



    def extract_token(self, request):
        auth_header = request.headers.get('Authorization')
        auth_token = ''
        if auth_header:
            auth_token = auth_header.split(" ")[1]
        return auth_token

    def not_valid_role(self, has_role, needs_role):
        has_roles=[]
        logging.debug(f"Checking {has_role} against {needs_role}")
        if has_role == 'admin':
            has_roles = ['admin', 'editor', 'viewer']
        if has_role == 'editor':
            has_roles = ['editor', 'viewer']
        if has_role == 'viewer':
            has_roles = ['viewer']
        logging.debug(f"List of roles:")
        logging.debug(has_roles)
        if (needs_role in has_roles):
            return False
        else:
            return True

    def extract_user(self, decoded):
        user = {
            "id":    decoded['sub'],
            "name":  decoded['name'],
            "email": decoded['email']
        }
        return user

    def extract_service_account(self, decoded):
        sa = {
            "id": decoded['id'],
            "name": decoded['name']
        }
        return sa

    def check_employee_role(self, min_role, request):
        try:
            auth_token = self.extract_token(request)
            decoded = self.decode(auth_token)
            if not decoded:
                return (401, "No Valid Auth Token Provided")
            if decoded:
                if 'sub' in decoded:
                    e_role = self.employee_role(self.service, decoded)
                    if self.not_valid_role(e_role, min_role):
                        return (403, "You do not have the role required for this action.", self.extract_user(decoded))
                    return (200, None, self.extract_user(decoded))
                else:
                    s_role = self.internal_service_role(decoded)
                    if self.not_valid_role(s_role, min_role):
                        return (403, "This internal service account does not have the role required for this action.", self.extract_service_account(decoded))
                    return (200, None, self.extract_service_account(decoded))
        except jwt.ExpiredSignatureError:
            return (401, "Your token has expired.  Please log in again.", {})
        except jwt.DecodeError:
            return (401, "Your token is invalid", {})


    def check_user_role(self, min_role, org_id, request):
        try:
            auth_token = self.extract_token(request)
            decoded = self.decode(auth_token)
            if not decoded:
                return (401, "No Valid Auth Token Provided", {})
            if decoded:
                if 'sub' in decoded:
                    #this is a user
                    u_role = self.user_role(org_id, decoded)
                    if self.not_valid_role(u_role, min_role):
                        return (403, "You do not have the role required for this action.", self.extract_user(decoded))
                    return (200, None, self.extract_user(decoded))
                else:
                    #this is a service account
                    s_role = self.service_role(org_id, decoded)
                    if self.not_valid_role(s_role, min_role):
                        return (403, "This service account does not have the role required for this action.", self.extract_service_account(decoded))
                    return (200, None, self.extract_service_account(decoded))
        except jwt.ExpiredSignatureError:
            return (401, "Your token has expired.  Please log in again.", {})
        except jwt.DecodeError:
            return (401, "Your token is invalid.", {})

    def check_user_id(self, user_id, request):
        try:
            auth_token = self.extract_token(request)
            decoded = self.decode(auth_token)
            if not decoded:
                return (401, "No Valid Auth Token Provided", {})
            if decoded:
                if self.user_id_matches(user_id, decoded):
                    return (200, None, self.extract_user(decoded))
                else:
                    return (403, "You are not the user you are looking for.", self.extract_user(decoded))
        except jwt.ExpiredSignatureError:
            return (401, "Your token has expired.  Please log in again.", {})
        except jwt.DecodeError:
            return (401, "Your token is invalid", {})

    def internal_service_role(self, decoded):
        try:
            i_roles = decoded['internal_roles'] if 'internal_roles' in decoded else []
            for i_role in i_roles:
                (t_service, level) = i_role.split(":")
                if t_service == self.service:
                    return level
        except Exception as e:
            logging.exception(e)
            return None
        return None

    def service_role(self, org_id, decoded):
        try:
            roles = decoded['roles']
            for role in roles:
                (t_org, level) = role.split(":")
                if t_org == "*": #employee, can work across all roles
                    return level
                if t_org == org_id:
                    return level
        except Exception as e:
            logging.exception(e)
            return None
        return None
