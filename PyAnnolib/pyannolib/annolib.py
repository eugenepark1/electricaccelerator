# Copyright (c) 2013 by Cisco Systems, Inc.
"""
Handle the emake annotation file.
"""
from pyannolib import concatfile
import xml.sax

#from xml.etree import ElementTree as ET
from xml.etree import cElementTree as ET

import types
import os

# Job status values
JOB_STATUS_NORMAL = "normal"
JOB_STATUS_RERUN = "rerun"
JOB_STATUS_CONFLICT = "conflict"
JOB_STATUS_REVERTED = "reverted"
JOB_STATUS_SKIPPED = "skipped"

# Job type values
JOB_TYPE_CONTINUATION = "continuation"
JOB_TYPE_END = "end"
JOB_TYPE_EXIST = "exist"
JOB_TYPE_EXTERNAL = "external"
JOB_TYPE_FOLLOW = "follow"
JOB_TYPE_PARSE = "parse"
JOB_TYPE_REMAKE = "remake"
JOB_TYPE_RULE = "rule"
JOB_TYPE_SUBBUILD = "subbuild"
JOB_TYPE_ALPHA = "alpha"
JOB_TYPE_OMEGA = "omega"
JOB_TYPE_UPDATE = "update"

# Dep type values
DEP_TYPE_FILE = "file"
DEP_TYPE_KEY = "key"
DEP_TYPE_VALUE = "value"

# Op type values
OP_TYPE_LOOKUP = "lookup"
OP_TYPE_READ = "read"
OP_TYPE_CREATE = "create"
OP_TYPE_MODIFY = "modify"
OP_TYPE_UNLINK = "unlink"
OP_TYPE_RENAME = "rename"
OP_TYPE_LINK = "link"
OP_TYPE_MODIFYATTRS = "modifyAttrs"
OP_TYPE_APPEND = "append"
OP_TYPE_BLINDCREATE = "blindcreate"
OP_TYPE_SUBMAKE = "submake"

# Op filetype values
OP_FILETYPE_FILE = "file"
OP_FILETYPE_SYMLINK = "symlink"
OP_FILETYPE_DIR = "dir"

# Op booleans
OP_FOUND_TRUE = "1"
OP_FOUND_FALSE = "0"
OP_ISDIR_TRUE = "1"
OP_ISDIR_FALSE = "0"

# Output sources
OUTPUT_SRC_PROG = "prog"
OUTPUT_SRC_MAKE = "make"


# Message severities
MESSAGE_SEVERITY_WARNING = "warning"
MESSAGE_SEVERITY_ERROR = "error"


# Show debug statements for parsing XML?
DEBUG_XML = False

# Some error strings
MSG_UNEXPECTED_XML_ELEM = "Unexpected xml element: "

class PyAnnolibError(Exception):
    """Generic exception for any error this library wants
    to pass back to the client"""
    pass

class FinishedHeaderException(Exception):
    """This is used internally for AnnoXMLHeaderHandler to
    stop processing the XML file mid-stream and resume executing
    back where it was called (in parseFH)."""
    pass

class AnnotatedBuild():
    ID = "id"
    CM = "cm"
    START = "start"
    ELEMENT_METRIC = "metric"
    ATTR_METRIC_NAME = "name"

    def __init__(self, filename, fh=None):
        """Can raise IOError"""
        # Initialize this since it is used in __str
        self.build_id = None

        # This is initialized now, but won't be filled in until
        # the very end of the file.
        self.metrics = {}

        # This is initialized now, but won't be filled in until
        # the message records are seen while processing jobs
        self.messages = []

        if filename:
            assert not fh, "filename and fh both given"

            # Allow the exception to go back to the caller of AnnoFileParser
            self.fh = anno_open(filename)

        elif fh:
            self.fh = fh

        else:
            assert 0, "Neither filename or fh given"

        # Do the parse
        self._parseFH()

    def close(self):
        """Closes the filehandle, which may be needed if you had
        AnnotatedBuild open() it for you."""
        assert self.fh, "fh has no value"
        self.fh.close()

    def __str__(self):
        return "<AnnotatedBuild id=%s>" % (self.build_id,)

    def _parseFH(self):
        """Parse the annotation file handle"""

        # Create the parser
        parser = xml.sax.make_parser()

        # Create the handler
        handler = AnnoXMLHeaderHandler()

        # Tell the parser to use our handler
        parser.setContentHandler(handler)

        # Don't fetch the DTD
        parser.setFeature(xml.sax.handler.feature_external_ges, False)

        # Parse the file
        try:
            parser.parse(self.fh)
        except FinishedHeaderException, exc:
            # After parsing the header, it throws an exception
            # so we can regain control here. We reset the file handle
            # and return the Build object to the user. The Build
            # object has it's own parse_jobs() function, which will
            # re-parse the annotation file, looking only at the "body"
            # of the file, not the "header".

            # The hdr_data tuple is the first argument to the exception,
            # which is what we need to store before we can parse
            # the jobs sequentially.
            hdr_data = exc.args[0]
            self._init_from_hdr_data(hdr_data)

            # Now, skip to the end of the file and look for metrics.
            self.parseMetrics()

            # Set the filehandle back to the beginning; when parseJobs()
            # is run, it uses AnnoXMLBodyParser, which skips over all the XML
            # fields that are part of the header. It's not the most efficient
            # way to handle the separation of body and header; it would be
            # much nicer to skip over the header and start parsing where
            # the body starts, but the innards of xml.sax are complicated,
            # and the header itself is rather small when compared to the
            # body, so it's not a horrible solution.
            self.fh.seek(0, os.SEEK_SET)

            # Done with processing the header, so return now.
            return

        # Because we caught FinishedHeaderException to return
        # the Build object to the user, we should not have
        # reached this point.
        raise ValueError("Should not have reached. Corrupt Build record.")


    def _init_from_hdr_data(self, hdr_data):
        """Use the header data to populte our data structures."""
        (xmlattrs, properties, vars) = hdr_data
        self.build_id = xmlattrs[self.ID]
        self.cm = xmlattrs[self.CM]
        self.start_text = xmlattrs[self.START]

        self.properties = properties
        self.vars = vars

    def parseMetrics(self):
        metrics_text = self._read_metrics_footer()

        # Parse the XML string
        try:
            root = ET.fromstring(metrics_text)
        except ET.ParseError, e:
            msg = "Error reading <metrics>: %s" % (e,)
            raise PyAnnolibError(msg)

        # Store the data in our dictionary
        for elem in list(root):
            if elem.tag == self.ELEMENT_METRIC:
                metric_name = elem.get(self.ATTR_METRIC_NAME)
                self.metrics[metric_name] = elem.text
            else:
                msg = UNEXPECTED_XML_ELEM + elem.tag
                raise PyAnnolibError(msg)

    def _read_metrics_footer(self):
        # Number of bytes to skip backwards at a time.
        # In my tests, the metrics section is ~3000 bytes.
        SKIP_NUM_BYTES = 500
        ELEM_STRING_START_METRICS = "<metrics>"
        ELEM_STRING_END_METRICS = "</metrics>"
        ELEM_STRING_END_JOB = "</job>"
        
        # When reading, we want to read more than what we just
        # skipped, so that if half the string is in one chunk, and
        # half the string is in the next chunk, we are sure to find
        # the string.
        READ_CHUNK_NUM_BYTES = SKIP_NUM_BYTES + len(ELEM_STRING_START_METRICS)

        # Go to EOF
        self.fh.seek(0, os.SEEK_END)

        pos = self.fh.tell()

        # Go back in chunks, until we find "<metrics>"
        pos -= SKIP_NUM_BYTES
        self.fh.seek(pos, os.SEEK_SET)
        while pos >= 0:
            data = self.fh.read(READ_CHUNK_NUM_BYTES)
            i = data.find(ELEM_STRING_START_METRICS)
            if i == -1:
                # Didn't find it. Are there any other indications
                # that we have gone too far? If we see a job, then yes.
                if data.find(ELEM_STRING_END_JOB) != -1:
                    return
                else:
                    pos -= SKIP_NUM_BYTES
                    self.fh.seek(pos, os.SEEK_SET)
            else:
                # We found it; stop looping
                break

        # We found "<metrics>". Let's read the whole "<metrics>"
        # section into memory. First, position the filehandle
        # to the place where "<metrics>" starts, then read to EOF.
        self.fh.seek(pos + i, os.SEEK_SET)
        metrics_text = self.fh.read()

        # We need to trim the stuff after "</metrics>",
        # which should be "</build>"
        i = metrics_text.find(ELEM_STRING_END_METRICS)
        if i == -1:
            msg = "Found %s but not %s" % (ELEM_STRING_START_METRICS,
                    ELEM_STRING_END_METRICS)
            raise PyAnnolibError(msg)

        metrics_text = metrics_text[:i + len(ELEM_STRING_END_METRICS)]
        return metrics_text


    def addMessage(self, msg):
        self.messages.append(msg)

    def getCM(self):
        return self.cm

    def getStart(self):
        return self.start_text

    def getBuildID(self):
        return self.build_id

    def getProperties(self):
        return self.properties

    def getVars(self):
        return self.vars

    def getMetrics(self):
        return self.metrics

    def getMessages(self):
        return self.messages

    def parseJobs(self, cb, user_data=None):
        """Parse jobs and call the callback for each Job object."""
        if not self.fh:
            raise ValueError("filehandle was not set in Build object")

        # Create the parser
        parser = AnnoXMLBodyParser(self, cb, user_data)

        # Parse the file
        parser.parse(self.fh)

    def getAllJobs(self):
        """Gather all Job records in a list and return that list.
        It's a convenience function; the same could be done via parseJobs()
        and the appropriate callback."""
        jobs = []

        def job_cb(job, junk):
            jobs.append(job)

        self.parseJobs(job_cb, None)

        return jobs

###################################################

class AnnoXMLNames:
    """These constants are used by both AnnoXMLHeaderHandler,
    and AnnoXMLBodyParser, but don't need to be global. So, they
    are in this base class, inherited by the two using classes."""
    # Found in the "header"
    ELEMENT_BUILD = "build"
    ELEMENT_PROPERTIES = "properties"
    ELEMENT_PROPERTY = "property"
    ELEMENT_ENVIRONMENT = "environment"
    ELEMENT_VAR = "var"

    # These attributes are handled by the AnnoXMLHeaderHandler directly
    ATTR_PROP_NAME = "name"
    ATTR_VAR_NAME = "name"

    # Found in the "body"
    ELEMENT_MAKE = "make"
    ELEMENT_JOB = "job"
    ELEMENT_OUTPUT = "output"
    ELEMENT_OPLIST = "opList"
    ELEMENT_OP = "op"
    ELEMENT_METRICS = "metrics"
    ELEMENT_METRIC = "metric"
    ELEMENT_TIMING = "timing"
    ELEMENT_WAITING_JOBS = "waitingJobs"
    ELEMENT_COMMAND = "command"
    ELEMENT_ARGV = "argv"
    ELEMENT_OUTPUT = "output"
    ELEMENT_DEPLIST = "depList"
    ELEMENT_DEP = "dep"
    ELEMENT_FAILED = "failed"
    ELEMENT_CONFLICT = "conflict"
    ELEMENT_MESSAGE = "message"

    # These attributes are handled by the AnnoXMLBodyParser directly
    ATTR_WAITINGJOBS_IDLIST = "idList"
    ATTR_METRIC_NAME = "name"
    ATTR_OUTPUT_SRC = "src"
    ATTR_FAILED_CODE = "code"

########################

class AnnoXMLHeaderHandler(xml.sax.handler.ContentHandler, AnnoXMLNames):
    """This sax parser handles the "header" portion of the annotation
    XML file, before the build jobs start."""
    def __init__(self):
        self.chars = ""
        self.property_name = None
        self.var_name = None
        self.indent = 0

        self.build_xmlattrs = None
        self.properties = {}
        self.vars = {}

    def startElement(self, name, xmlattrs):
        self.chars = ""

        if name == self.ELEMENT_BUILD:
            self.build_xmlattrs = xmlattrs

        elif name == self.ELEMENT_PROPERTIES:
            pass

        elif name == self.ELEMENT_PROPERTY:
            self.property_name = xmlattrs[self.ATTR_PROP_NAME]

        elif name == self.ELEMENT_ENVIRONMENT:
            pass

        elif name == self.ELEMENT_VAR:
            self.var_name = xmlattrs[self.ATTR_VAR_NAME]



    def endElement(self, name):
        if name == self.ELEMENT_PROPERTY:
            self.properties[self.property_name] = self.chars
            self.property_name = None

        elif name == self.ELEMENT_VAR:
            self.vars[self.var_name] = self.chars
            self.var_name = None

        # at the end if <environment> we can pass back
        # the header fields
        elif name  == self.ELEMENT_ENVIRONMENT:
            hdr_data = (self.build_xmlattrs, self.properties, self.vars)
            raise FinishedHeaderException(hdr_data)

        self.chars = ""


    def characters(self, chars):
        self.chars += chars



####################################################

        


class MakeProcess:

    LEVEL = "level"
    CMD = "cmd"
    CWD = "cwd"
    OWD = "owd"
    MODE = "mode"

    def __init__(self, elem, make_proc_num, parser):
        self.level = elem.get(self.LEVEL)
        self.cmd = elem.get(self.CMD)
        self.cwd = elem.get(self.CWD)
        self.owd = elem.get(self.OWD) # implied, not required
        self.mode = elem.get(self.MODE)

        # This is not stored as a field in the XML file; it is
        # constructed by noting the sequential order of <job>'s
        # and <make>'s  in the file. The <job> just previous to
        # a <make> is the <make>'s parent job.
        self.parent_job_id = None

        self.make_proc_id = "M%08d" % (make_proc_num,)


    def __str__(self):
        return "<MakeProcess level=%s cwd=%s>" % (self.level, self.cwd)

    def setParentJobID(self, job_id):
        self.parent_job_id = job_id

    def getLevel(self):
        return self.level

    def getCmd(self):
        return self.cmd

    def getCWD(self):
        return self.cwd

    def getOWD(self):
        return self.owd

    def getMode(self):
        return self.mode

    def getParentJobID(self):
        return self.parent_job_id

    def getID(self):
        return self.make_proc_id

class Job(AnnoXMLNames):

    ID = "id"
    STATUS  ="status"
    THREAD = "thread"
    TYPE = "type"
    NAME = "name"
    NEEDED_BY = "neededby"
    LINE = "line"
    FILE = "file"
    SUCCESS = 0
    PARTOF = "partof"   # for FOLLOW-type jobs

    def __init__(self, elem):
        self.job_id = elem.get(self.ID)
        self.status = elem.get(self.STATUS, JOB_STATUS_NORMAL)
        self.thread = elem.get(self.THREAD)
        self.type = elem.get(self.TYPE)
        self.name = elem.get(self.NAME)
        self.needed_by = elem.get(self.NEEDED_BY)
        self.line = elem.get(self.LINE)
        self.file = elem.get(self.FILE)
        self.partof = elem.get(self.PARTOF)

        self.outputs = []
        self.make = None
        self.timings = []
        self.oplist = []
        self.waiting_jobs = []
        self.commands = []
        self.deplist = []
        self.conflict = None

        # The return value of the Job. By default
        # we assume success, but a <failed> record overrides that.
        self.retval = self.SUCCESS

        for child_elem in list(elem):
            if child_elem.tag == self.ELEMENT_TIMING:
                timing = Timing(child_elem)
                self.timings.append(timing)

            elif child_elem.tag == self.ELEMENT_OPLIST:
                self.parseOpList(child_elem)

            elif child_elem.tag == self.ELEMENT_WAITING_JOBS:
                self.parseWaitingJobs(child_elem)

            elif child_elem.tag == self.ELEMENT_COMMAND:
                command = Command(child_elem)
                self.commands.append(command)

            elif child_elem.tag == self.ELEMENT_OUTPUT:
                # This is the job output, not the command output
                output_src = child_elem.get(self.ATTR_OUTPUT_SRC,
                        OUTPUT_SRC_MAKE)
                output = Output(child_elem.text, output_src)
                self.outputs.append(output)

            elif child_elem.tag == self.ELEMENT_FAILED:
                code_text = child_elem.get(self.ATTR_FAILED_CODE)
                self.retval = int(code_text)

            elif child_elem.tag == self.ELEMENT_CONFLICT:
                self.conflict = Conflict(child_elem)

            elif child_elem.tag == self.ELEMENT_DEPLIST:
                self.parseDepList(child_elem)


            else:
                assert False, MSG_UNEXPECTED_XML_ELEM + child_elem.tag

    def __str__(self):
        return "<Job id=%s>" % (self.job_id,)

    def parseOpList(self, elem):
        for child_elem in list(elem):
            if child_elem.tag == self.ELEMENT_OP:
                op = Operation(child_elem)
                self.oplist.append(op)

            else:
                assert False, MSG_UNEXPECTED_XML_ELEM + child_elem.tag

    def parseDepList(self, elem):
        for child_elem in list(elem):
            if child_elem.tag == self.ELEMENT_DEP:
                dep = Dependency(child_elem)
                self.deplist.append(dep)

            else:
                assert False, MSG_UNEXPECTED_XML_ELEM + child_elem.tag

    def parseWaitingJobs(self, elem):
        ids_string = elem.get(self.ATTR_WAITINGJOBS_IDLIST)

        # The job IDs are space-delimited in the string;
        # use split() to create a list
        self.waiting_jobs = ids_string.split()

    def setMakeProcess(self, make_elem):
        self.make = make_elem

    def getID(self):
        return self.job_id

    def getStatus(self):
        return self.status

    def getThread(self):
        return self.thread

    def getType(self):
        return self.type

    def getOutputs(self):
        return self.outputs

    def getOperations(self):
        return self.oplist

    def getMakeProcess(self):
        return self.make

    def getTimings(self):
        return self.timings

    def getWaitingJobs(self):
        return self.waiting_jobs

    def getCommands(self):
        return self.commands

    def getDependencies(self):
        return self.deplist

    def getRetval(self):
        return self.retval

    def getConflict(self):
        return self.conflict

    def getName(self):
        return self.name

    def getNeededBy(self):
        return self.needed_by

    def getFile(self):
        return self.file

    def getLine(self):
        return self.line

    def getPartOf(self):
        return self.partof


class Operation:
    TYPE = "type"
    FILE = "file"
    FILETYPE = "filetype"
    FOUND = "found"
    ISDIR = "isdir"

    def __init__(self, elem):
        self.type = elem.get(self.TYPE)
        self.file = elem.get(self.FILE)
        self.filetype = elem.get(self.FILETYPE, OP_FILETYPE_FILE)
        self.found = elem.get(self.FOUND, OP_FOUND_TRUE)
        self.isdir = elem.get(self.ISDIR, OP_ISDIR_TRUE)

    def getType(self):
        return self.type

    def getFile(self):
        return self.file

    def getFileType(self):
        return self.filetype

    def getFound(self):
        return self.found

class Timing:
    INVOKED = "invoked"
    COMPLETED = "completed"
    NODE = "node"

    def __init__(self, elem):
        self.invoked = elem.get(self.INVOKED)
        self.completed = elem.get(self.COMPLETED)
        self.node = elem.get(self.NODE)

    def getInvoked(self):
        return self.invoked

    def getCompleted(self):
        return self.completed

    def getNode(self):
        return self.node

class Command(AnnoXMLNames):
    LINE = "line"

    def __init__(self, elem):
        # "line" is optional
        self.line = elem.get(self.LINE)
        self.argv = ""
        self.outputs = []

        for child_elem in list(elem):
            if child_elem.tag == self.ELEMENT_ARGV:
                assert not self.argv
                self.argv = child_elem.text

            elif child_elem.tag == self.ELEMENT_OUTPUT:
                # This is the command output, not the job output
                output_src = child_elem.get(self.ATTR_OUTPUT_SRC,
                        OUTPUT_SRC_MAKE)
                output = Output(child_elem.text, output_src)
                self.outputs.append(output)

            else:
                assert False, MSG_UNEXPECTED_XML_ELEM + child_elem.tag

    def getLine(self):
        return self.line

    def getArgv(self):
        return self.argv

    def getOutputs(self):
        return self.outputs

class Output:
    def __init__(self, text, src):
        self.text = text
        self.src = src

    def getText(self):
        return self.text

    def getSrc(self):
        return self.src


class Dependency:
    WRITE_JOB = "writejob"
    FILE = "file"
    TYPE = "type"

    def __init__(self, elem):
        self.write_job = elem.get(self.WRITE_JOB)
        self.file = elem.get(self.FILE)
        self.type = elem.get(self.TYPE, DEP_TYPE_FILE)

    def getWriteJob(self):
        return self.write_job

    def getFile(self):
        return self.file

    def getType(self):
        return self.type

class Conflict:
    TYPE = "type"
    WRITE_JOB = "writejob"
    FILE = "file"
    RERUN_BY = "rerunby"

    def __init__(self, elem):
        self.type = elem.get(self.TYPE)
        self.write_job = elem.get(self.WRITE_JOB)
        self.file = elem.get(self.FILE)
        self.rerun_by = elem.get(self.RERUN_BY)

    def getType(self):
        return self.type

    def getWriteJob(self):
        return self.write_job

    def getFile(self):
        return self.file

    def getRerunBy(self):
        return self.rerun_by

class Message:
    THREAD = "thread"
    TIME = "time"
    SEVERITY = "severity"
    CODE = "code"

    def __init__(self, elem):
        self.thread = elem.get(self.THREAD)
        self.time = elem.get(self.TIME)
        self.severity = elem.get(self.SEVERITY)
        self.code = elem.get(self.CODE)
        self.text = None

    def setText(self, text):
        self.text = text

    def getText(self):
        return self.text

    def getThread(self):
        return self.thread

    def getTime(self):
        return self.time

    def getSeverity(self):
        return self.severity

    def getCode(self):
        return self.code


class AnnoXMLBodyParser(AnnoXMLNames):

    def __init__(self, build, cb, user_data):
        self.build = build
        self.cb = cb
        self.user_data = user_data
        self.chars = ""
        self.indent = 0

        # In local mode builds, make elements can nest,
        # so this list of make_elem's is a stack.
        self.make_elems = []

        # The previous job element that was completely parsed
        # This is needed to associate a "parent" job to a MakeProcess
        self.prev_job_elem = None

        # Make processes don't have ID's in the XML file. We give
        # them sequential ID's according to the order we discover
        # them in the XML file. The root <make> is #0.
        self.make_proc_num = 0

        self.metrics = {}


    def parse(self, fh):
    
        # The XML looks like this:
        # <make>
        #   <job></job>
        #   <job></job>
        #   <make>
        #       <job></job>
        #       <job></job>
        #       <make>
        #           <job></job>
        #           <job></job>
        #       </make>
        #       <job></job>
        #   </make>
        #   <job></job>
        # <make>
        #
        # We want to grab the end of a <job> element, so we
        # can parse it fully and send it back to the user.
        # But we want to grab the start and end of a <make> element,
        # so that we know which MakeProcess a Job belongs to.
        # The last thing we want is to read the entire <make> tree
        # into memory, which will read in all the jobs!

        # http://effbot.org/zone/element-iterparse.htm#incremental-parsing
        START_EVENT = "start"
        END_EVENT = "end"
        icontext = ET.iterparse(fh, events=(START_EVENT, END_EVENT))

        # Turn the context into an iterator
        context = iter(icontext)

        # Get the root element
        event, root = context.next()

        #for action, elem in ET.iterparse(fh):
        for event, elem in context:
            if event == START_EVENT:
                if elem.tag == self.ELEMENT_MAKE:
                    self.startMake(elem)
                    continue
                else:
                    # Skip all other START events
                    continue

            # Everything else is an END event

            if elem.tag == self.ELEMENT_JOB:
                assert len(self.make_elems) > 0
                job = Job(elem)

                job.setMakeProcess(self.make_elems[-1])

                # Send the Job back to the user
                self.cb(job, self.user_data)

                # Set the "previous job" to this job, so that
                # we know which job begins a Make process.
                self.prev_job_elem = job

            elif elem.tag == self.ELEMENT_MESSAGE:
                msg = Message(elem)
                self.build.addMessage(msg)

            elif elem.tag == self.ELEMENT_MAKE:
                assert len(self.make_elems) > 0
                self.make_elems.pop()

            # Explicitly skip elements that are sub-elements
            # of Job
            elif elem.tag == self.ELEMENT_TIMING:
                continue
            elif elem.tag == self.ELEMENT_OPLIST:
                continue
            elif elem.tag == self.ELEMENT_OP:
                continue
            elif elem.tag == self.ELEMENT_ARGV:
                continue
            elif elem.tag == self.ELEMENT_OUTPUT:
                continue
            elif elem.tag == self.ELEMENT_COMMAND:
                continue
            elif elem.tag == self.ELEMENT_WAITING_JOBS:
                continue
            elif elem.tag == self.ELEMENT_FAILED:
                continue
            elif elem.tag == self.ELEMENT_CONFLICT:
                continue
            elif elem.tag == self.ELEMENT_DEPLIST:
                continue
            elif elem.tag == self.ELEMENT_DEP:
                continue

            # Explicitly skip the elements found only in the metrics
            elif elem.tag == self.ELEMENT_METRIC:
                continue

            # Explicitly skip the elements found only in the header
            elif elem.tag == self.ELEMENT_PROPERTIES:
                continue
            elif elem.tag == self.ELEMENT_PROPERTY:
                continue
            elif elem.tag == self.ELEMENT_ENVIRONMENT:
                continue
            elif elem.tag == self.ELEMENT_VAR:
                continue

            # We handled metrics at the beginning of
            # the parse
            elif elem.tag == self.ELEMENT_METRICS:
                continue

            # Explicitly skip ourself!
            elif elem.tag == self.ELEMENT_BUILD:
                continue

            # Catch my mistakes
            else:
                assert False, MSG_UNEXPECTED_XML_ELEM + elem.tag

            # If we reached here, we processed an END event for an element.
            # Free the memory for this element
            elem.clear()


    def startMake(self, elem):
        make_elem = MakeProcess(elem, self.make_proc_num, self)

        self.make_proc_num += 1

        # All <make>'s must be preceded by a <job>, except for the
        # first make. If emake was run from the command-line, it's 0,
        # but if emake was run from inside a parent GNU Make,
        # then it's > 0. In order to see if this is the "root"
        # emake process, we cannot rely on getLevel(), as it could
        # be _anything_, so we simply check if it is the first
        # "<make>" element we found in the annotation file.
        if self.make_proc_num > 1:
            assert self.prev_job_elem
            if self.prev_job_elem.getType() == JOB_TYPE_FOLLOW:
                make_elem.setParentJobID(self.prev_job_elem.getPartOf())
            else:
                make_elem.setParentJobID(self.prev_job_elem.getID())

        self.make_elems.append(make_elem)




def anno_open(filename, mode="rb"):
    """Return either a Python file object, if there is only one
    annotation file, or a ConcatenatedFile object, which acts
    like a single file object, but magically combines multiple files."""
    # Fill in the array of file names
    filenames = [filename]

    READ = "r"
    READ_BINARY = "rb"

    N = 1
    while True:
        looking_for = filename + "_" + str(N)
        if os.path.exists(looking_for):
            filenames.append(looking_for)
            N += 1
        else:
            # No more files
            break

    if len(filenames) == 1:
        return open(filename, mode)
    else:
        if not mode in [READ, READ_BINARY]:
            raise ValueError("Only read-only mode is supported.")
        return concatfile.ConcatenatedFile(filenames, mode)
