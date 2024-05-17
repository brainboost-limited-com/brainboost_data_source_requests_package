from src.Request import Request
from src.ProxyPool import ProxyPool
from src.UserAgentPool import UserAgentPool

class ProxyRequest(Request):

    def __init__(self):
        self.proxy_pool = ProxyPool()
        self._useragent_database = UserAgentPool()
        super.__init__(self,timeout=10, proxy=self.proxy_pool.get_best_proxy(),user_agent=self._useragent_database.get_random_user_agent())
        

    def get(self,page,data={}):
        return super.get(page)

    def post(self,page,data={}):
        return super.post(page,data)