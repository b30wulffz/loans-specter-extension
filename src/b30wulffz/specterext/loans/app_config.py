"""
Here you can put the Configuration of your Application
"""

import os
from cryptoadvance.specter.config import ProductionConfig as SpecterProductionConfig

class AppProductionConfig(SpecterProductionConfig):
    ''' The AppProductionConfig class can be used to user this extension as application
    '''
    # Where should the User endup if he hits the root of that domain?
    ROOT_URL_REDIRECT = "/spc/ext/loans"
    # I guess this is the only extension which should be available?
    EXTENSION_LIST = [
        "b30wulffz.specterext.loans.service"
    ]
    # You might also want a different folder here
    SPECTER_DATA_FOLDER=os.path.expanduser("~/.loans")