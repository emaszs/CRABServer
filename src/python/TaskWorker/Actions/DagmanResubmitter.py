
import base64
import urllib
import traceback

import classad
import htcondor

import HTCondorLocator
import HTCondorUtils

import TaskWorker.Actions.TaskAction as TaskAction

from httplib import HTTPException

class DagmanResubmitter(TaskAction.TaskAction):

    """
    Given a task name, resubmit failed tasks.

    Internally, we simply release the failed DAG.
    """

    def execute_internal(self, *args, **kw):

        if 'task' not in kw:
            raise ValueError("No task specified.")
        task = kw['task']
        if 'tm_taskname' not in task:
            raise ValueError("No taskname specified.")
        workflow = str(task['tm_taskname'])
        if 'user_proxy' not in task:
            raise ValueError("No proxy provided")
        proxy = task['user_proxy']

        self.logger.info("About to resubmit workflow: %s." % workflow)
        self.logger.info("Task info: %s" % str(task))

        loc = HTCondorLocator.HTCondorLocator(self.backendurls)
        schedd, address = loc.getScheddObj(workflow)

        # Release the DAG
        rootConst = "TaskType =?= \"ROOT\" && CRAB_ReqName =?= %s" % HTCondorUtils.quote(workflow)

        # Calculate a new white/blacklist
        ad = classad.ClassAd()
        ad['whitelist'] = task['resubmit_site_whitelist']
        ad['blacklist'] = task['resubmit_site_blacklist']

        if ('resubmit_ids' in task) and task['resubmit_ids']:
            ad['resubmit'] = task['resubmit_ids']
            with HTCondorUtils.AuthenticatedSubprocess(proxy) as (parent, rpipe):
                if not parent:
                    schedd.edit(rootConst, "HoldKillSig", 'SIGKILL')
                    schedd.edit(rootConst, "CRAB_ResubmitList", ad['resubmit'])
                    schedd.act(htcondor.JobAction.Hold, rootConst)
                    schedd.edit(rootConst, "HoldKillSig", 'SIGUSR1')
                    schedd.act(htcondor.JobAction.Release, rootConst)

        if task['resubmit_site_whitelist'] or task['resubmit_site_blacklist']:
            with HTCondorUtils.AuthenticatedSubprocess(proxy) as (parent, rpipe):
                if not parent:
                    if task['resubmit_site_blacklist']:
                        schedd.edit(rootConst, "CRAB_SiteResubmitBlacklist", ad['blacklist'])
                    if task['resubmit_site_whitelist']:
                        schedd.edit(rootConst, "CRAB_SiteResubmitWhitelist", ad['whitelist'])
                    schedd.act(htcondor.JobAction.Release, rootConst)
        results = rpipe.read()
        if results != "OK":
            raise Exception("Failure when killing job: %s" % results)


    def execute(self, *args, **kwargs):

        try:
            return self.execute_internal(*args, **kwargs)
        except Exception, e:
            msg = "Task %s resubmit failed: %s." % (kwargs['task']['tm_taskname'], str(e))
            self.logger.error(msg)
            configreq = {'workflow': kwargs['task']['tm_taskname'],
                         'status': "FAILED",
                         'subresource': 'failure',
                         'failure': base64.b64encode(msg)}
            try:
                self.server.post(self.resturl, data = urllib.urlencode(configreq))
            except HTTPException, hte:
                self.logger.error(str(hte.headers))
            raise
        finally:
            configreq = {'workflow': kwargs['task']['tm_taskname'],
                         'status': "SUBMITTED",
                         'jobset': "-1",
                         'subresource': 'success',}
            self.logger.debug("Setting the task as successfully resubmitted with %s " % str(configreq))
            data = urllib.urlencode(configreq)
            self.server.post(self.resturl, data = data)


