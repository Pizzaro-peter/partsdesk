import os,json,unittest,hashlib,sys
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.test.runner import DiscoverRunner

out=Path(os.environ.get('PARTSDESK_UAT_OUTPUT',str(Path(__file__).resolve().parent/'uat_results')));out.mkdir(exist_ok=True)
class Result(unittest.TextTestResult):
    def startTest(self,test):
        self.current=str(test);super().startTest(test)
    def addSuccess(self,test):
        results.append({'test':test.id(),'result':'Pass'});super().addSuccess(test)
    def addFailure(self,test,err):
        results.append({'test':test.id(),'result':'Fail','details':self._exc_info_to_string(err,test)});super().addFailure(test,err)
    def addError(self,test,err):
        results.append({'test':test.id(),'result':'Error','details':self._exc_info_to_string(err,test)});super().addError(test,err)
    def addSkip(self,test,reason):
        results.append({'test':test.id(),'result':'Skipped','details':reason});super().addSkip(test,reason)
results=[]
class Runner(DiscoverRunner):
    def get_resultclass(self):return Result
runner=Runner(verbosity=1,interactive=False)
failures=runner.run_tests(['core.tests','uat_regression','uat_fixes','uat_excel','uat_navigation','uat_audit'])
(out/'results.json').write_text(json.dumps({'django':django.get_version(),'python':sys.version,'results':results},indent=2))
print('RESULT COUNTS:',{r:sum(x['result']==r for x in results) for r in ['Pass','Fail','Error','Skipped']})
sys.exit(bool(failures))
