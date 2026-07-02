from .cloudcc_crm import CloudCCCrmPolicy
from .data_cleaning_file_organization import DataCleaningFileOrganizationPolicy


_POLICIES = {
    "cloudcc_crm": CloudCCCrmPolicy(),
    "data_cleaning_file_organization": DataCleaningFileOrganizationPolicy(),
}


def get_policy(name):
    return _POLICIES.get(name)
