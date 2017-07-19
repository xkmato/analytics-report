import urllib2
import hashlib
import urllib
import json
import operator
from django.db.models import Q
from django.db import models
from django.conf import settings
from temba_client.v2 import TembaClient
from django.core.mail import EmailMessage
import datetime
from django.utils import timezone
import pytz

tz = 'Africa/Kampala'


class RapidproKey(models.Model):
    workspace = models.CharField(max_length=200)
    host = models.CharField(max_length=200)
    key = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now=False, auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True, auto_now_add=False)

    @classmethod
    def get_rapidpro_groups(cls):
        keys = cls.objects.all()
        for rkey in keys:
            client = TembaClient(rkey.host, rkey.key)
            Group.add_groups(client=client)
            Contact.save_contacts(client=client)
            Flow.add_flows(client=client)

    def __unicode__(self):
        return str(self.workspace)


class Group(models.Model):
    uuid = models.CharField(max_length=200)
    name = models.CharField(max_length=200)
    count = models.IntegerField()
    created_at = models.DateTimeField(auto_now=False, auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True, auto_now_add=False)

    @classmethod
    def add_groups(cls, client):
        added = 0
        for group_batch in client.get_groups().iterfetches(retry_on_rate_exceed=True):
            for group in group_batch:
                if cls.group_exists(group):
                    cls.objects.filter(uuid=group.uuid).update(name=group.name, count=group.count)
                    added += 0

                else:
                    cls.objects.create(uuid=group.uuid, name=group.name, count=group.count)
                    added += 1
                    # Flow.add_flows()  # remember to put in celery
        return added

    @classmethod
    def group_exists(cls, group):
        return cls.objects.filter(uuid=group.uuid).exists()

    @classmethod
    def get_all_groups(cls):
        return cls.objects.all()

    def __unicode__(self):
        return str(self.name)


class Project(models.Model):
    name = models.CharField(max_length=200)
    group = models.ManyToManyField(Group, related_name='groups')
    lead = models.CharField(max_length=200)
    active = models.BooleanField(default=False)
    created_on = models.DateTimeField(auto_now=False, auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True, auto_now_add=False)

    @classmethod
    def get_project_data(cls, name):
        return cls.objects.filter(name=name, active=True).all()

    @classmethod
    def get_all_projects(cls):
        return cls.objects.filter(active=True).all()

    def __unicode__(self):
        return str(self.name)


class Contact(models.Model):
    uuid = models.CharField(max_length=200)
    name = models.CharField(max_length=200, null=True, blank=True)
    language = models.CharField(max_length=200, null=True)
    urns = models.CharField(max_length=200)
    groups = models.TextField()
    fields = models.CharField(max_length=200, null=True, blank=True)
    blocked = models.BooleanField(default=False)
    stopped = models.BooleanField(default=False)
    created_on = models.DateTimeField(null=True)
    modified_on = models.DateTimeField(null=True)

    @classmethod
    def save_contacts(cls, client):
        added = 0
        for contact_batch in client.get_contacts().iterfetches(retry_on_rate_exceed=True):
            for contact in contact_batch:
                grp = []
                fld = []
                for g in contact.groups:
                    grp.append(g.name)

                for f in contact.fields:
                    fld.append((f, contact.fields[f]))

                if cls.contact_exists(contact):
                    con = cls.objects.get(uuid=contact.uuid)
                    for gp in con.groups:
                        if gp in grp:
                            grp.remove(gp)
                        else:
                            grp.append(gp)

                    for fd in con.fields:
                        if fd in fld:
                            fld.remove(fd)
                        else:
                            fld.append(fd)

                    ct = cls.objects.filter(uuid=contact.uuid).update(name=contact.name, language=contact.language,
                                                                      urns=cls.clean_contacts(contact), groups=grp,
                                                                      fields=fld,
                                                                      blocked=contact.blocked, stopped=contact.stopped,
                                                                      created_on=contact.created_on,
                                                                      modified_on=contact.modified_on)
                    Message.save_messages(client, contact=ct)
                    Run.add_runs(client, contact=con)
                    grp[:] = []
                    fld[:] = []

                else:
                    ct = cls.objects.create(uuid=contact.uuid, name=contact.name, language=contact.language,
                                            urns=cls.clean_contacts(contact), groups=grp, fields=fld,
                                            blocked=contact.blocked, stopped=contact.stopped,
                                            created_on=contact.created_on, modified_on=contact.modified_on)
                    Message.save_messages(client, contact=ct)
                    Run.add_runs(client, contact=ct)
                    grp[:] = []
                    fld[:] = []

                    added += 1

        return added

    @classmethod
    def contact_exists(cls, contact):
        return cls.objects.filter(uuid=contact.uuid).exists()

    @classmethod
    def urns_exists(cls, number):
        return cls.objects.filter(urns=number).exists()

    @classmethod
    def get_project_contacts(cls, project_list):
        query = reduce(operator.or_, (Q(groups__contains=item) for item in project_list))
        return cls.objects.filter(query).all()

    @classmethod
    def get_project_contacts_count(cls, project_list):
        query = reduce(operator.or_, (Q(groups__contains=item) for item in project_list))
        return cls.objects.filter(query).count()

    @classmethod
    def get_weekly_project_contacts(cls, project_list):
        query = reduce(operator.or_, (Q(groups__contains=item) for item in project_list))
        date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
        return cls.objects.filter(query, created_on__range=(date_diff, datetime.datetime.now())).all()

    @classmethod
    def get_all_project_contacts_value_list(cls, project_list):
        query = reduce(operator.or_, (Q(groups__contains=item) for item in project_list))
        return cls.objects.filter(query).values_list('urns')

    @classmethod
    def get_contacts_count(cls):
        date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
        return cls.objects.filter(created_on__range=(date_diff, datetime.datetime.now())).count()

    @classmethod
    def clean_contacts(cls, contact):
        for c in contact.urns:
            if 'tel:' in c:
                return c[4:]
            else:
                return contact.urns

    def __unicode__(self):
        return str(self.urns)


class Message(models.Model):
    msg_id = models.IntegerField()
    broadcast = models.IntegerField(null=True)
    contact = models.ForeignKey(Contact)
    urn = models.CharField(max_length=200)
    channel = models.CharField(max_length=200)
    direction = models.CharField(max_length=200)
    type = models.CharField(max_length=200)
    status = models.CharField(max_length=200)
    visibility = models.CharField(max_length=200)
    text = models.CharField(max_length=1000)
    labels = models.CharField(max_length=200)
    created_on = models.DateTimeField(auto_now_add=True, editable=False)
    sent_on = models.DateTimeField(null=True, blank=True)
    modified_on = models.DateTimeField(null=True, blank=True)

    @classmethod
    def save_messages(cls, client, contact):
        added = 0

        for message_batch in client.get_messages(contact=contact.uuid).iterfetches(retry_on_rate_exceed=True):
            for message in message_batch:
                if not cls.message_exists(message):
                    cls.objects.create(msg_id=message.id, broadcast=message.broadcast, contact=contact,
                                       urn=cls.clean_msg_contacts(message), channel=message.channel,
                                       direction=message.direction,
                                       type=message.type, status=message.status, visibility=message.visibility,
                                       text=message.text, labels=message.labels, created_on=message.created_on,
                                       sent_on=message.sent_on, modified_on=message.modified_on)
                    added += 1
                else:
                    pass

        return added

    @classmethod
    def message_exists(cls, message):
        return cls.objects.filter(msg_id=message.id).exists()

    @classmethod
    def get_sent_messages(cls):
        date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
        return cls.objects.filter(direction='out', sent_on__range=(date_diff, datetime.datetime.now())).all()

    @classmethod
    def get_delivered_messages(cls):
        date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
        return cls.objects.filter(direction='out', status='delivered',
                                  sent_on__range=(date_diff, datetime.datetime.now())).all()

    @classmethod
    def get_failed_messages(cls):
        date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
        return cls.objects.filter(direction='out', sent_on__range=(date_diff, datetime.datetime.now())).all() \
            .exclude(status='delivered').all()

    @classmethod
    def sent_messages_count(cls):
        date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
        return cls.objects.filter(direction='out', sent_on__range=(date_diff, datetime.datetime.now())).count()

    @classmethod
    def count_read_messages(cls):
        date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
        return cls.objects.filter(status='delivered', direction='out',
                                  sent_on__range=(date_diff, datetime.datetime.now())).count()

    @classmethod
    def count_unread_messages(cls):
        date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
        return cls.objects.filter(direction='out', sent_on__range=(date_diff, datetime.datetime.now())) \
            .exclude(status='delivered').count()

    @classmethod
    def get_unread_messages(cls):
        date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
        return cls.objects.filter(direction='out', status='errored',
                                  sent_on__range=(date_diff, datetime.datetime.now())).all()

    @classmethod
    def get_failed_messages_daily(cls):
        date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
        return cls.objects.filter(direction='out', status='sent',
                                  sent_on__range=(date_diff, datetime.datetime.now())).all()

    @classmethod
    def get_weekly_failed_messages_daily(cls, contact_list):
        # query = reduce(operator.or_, (Q(contact__groups__contains=item) for item in project_list))
        # query_2 = reduce(operator.or_, (Q(contact__in=item) for item in contact_qs))
        query_3 = reduce(operator.or_, (Q(urn__contains=item) for item in contact_list))
        date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
        return cls.objects.filter(direction='out', status='failed',
                                  sent_on__range=(date_diff, datetime.datetime.now())).all() \
 \
    # @classmethod
    # def get_weekly_failed_messages_daily(cls, contact_list):
    #     # query = reduce(operator.or_, (Q(contact__groups__contains=item) for item in project_list))
    #     # query_2 = reduce(operator.or_, (Q(contact__in=item) for item in contact_qs))
    #     query_3 = reduce(operator.or_, (Q(urn__contains=item) for item in contact_list))
    #     date_diff = datetime.datetime.now() - datetime.timedelta(days=7)
    #     return cls.objects.filter(direction='out', status='delivered',
    #                               sent_on__range=(date_diff, datetime.datetime.now())).all()

    @classmethod
    def clean_msg_contacts(cls, msg):
        if 'tel:' in msg.urn:
            return msg.urn[4:]
        else:
            return msg.urn

    def __unicode__(self):
        return self.urn


class Flow(models.Model):
    uuid = models.CharField(max_length=100)
    name = models.CharField(max_length=100)
    expires = models.IntegerField()
    active_runs = models.IntegerField(null=True)
    complete_runs = models.IntegerField(null=True)
    interrupted_runs = models.IntegerField(null=True)
    expired_runs = models.IntegerField(null=True)
    created_on = models.DateTimeField()

    @classmethod
    def add_flows(cls, client):
        flows = client.get_flows().all()
        added = 0
        for flow in flows:
            if cls.flow_exists(flow):
                cls.objects.filter(uuid=flow.uuid).update(name=flow.name, expires=flow.expires,
                                                          active_runs=flow.runs.active,
                                                          complete_runs=flow.runs.completed,
                                                          interrupted_runs=flow.runs.interrupted,
                                                          expired_runs=flow.runs.expired, created_on=flow.created_on)
                added += 0
            else:
                cls.objects.create(uuid=flow.uuid, name=flow.name, expires=flow.expires,
                                   active_runs=flow.runs.active, complete_runs=flow.runs.completed,
                                   interrupted_runs=flow.runs.interrupted, expired_runs=flow.runs.expired,
                                   created_on=flow.created_on)
                added += 1

        return added

    @classmethod
    def flow_exists(cls, flow):
        return cls.objects.filter(uuid=flow.uuid).exists()

    def __unicode__(self):
        return self.name


class Run(models.Model):
    run_id = models.IntegerField()
    flow = models.CharField(max_length=200)
    contact = models.ForeignKey(Contact)
    responded = models.BooleanField(default=False)
    exit_type = models.CharField(max_length=100, null=True, blank=True)
    exited_on = models.DateTimeField(null=True)
    created_on = models.DateTimeField()
    modified_on = models.DateTimeField()

    @classmethod
    def add_runs(cls, client, contact):
        added = 0
        for run_batch in client.get_runs(contact=contact.uuid).iterfetches(retry_on_rate_exceed=True):
            for run in run_batch:
                if not cls.run_exists(run):
                    r = cls.objects.create(run_id=run.id, flow=run.flow, contact=contact, responded=run.responded,
                                           exit_type=run.exit_type, exited_on=run.exited_on,
                                           created_on=run.created_on, modified_on=run.modified_on)
                    added += 1

        return added

    @classmethod
    def run_exists(cls, run):
        return cls.objects.filter(run_id=run.id).exists()

    def __unicode__(self):
        return str(self.run_id)


# class Step(models.Model):
#     node = models.CharField(max_length=100)
#     time = models.DateTimeField()
#     run = models.ForeignKey(Run, on_delete=models.CASCADE)
#
#     @classmethod
#     def add_steps(cls, run, steps):
#         added = 0
#         for step in steps:
#             if not cls.step_exists(step):
#                 cls.objects.create(node=step.node, time=step.time, run=run)
#                 added += 1
#         return added
#
#     @classmethod
#     def step_exists(cls, step):
#         return cls.objects.filter(node=step.node).exists()
#
#     def _str__(self):
#         return str(self.node)
#
#
# class Value(models.Model):
#     value = models.CharField(max_length=100, blank=True)
#     run = models.ForeignKey(Run, on_delete=models.CASCADE)
#
#     @classmethod
#     def add_values(cls, run, values):
#         added = 0
#         for val in values:
#             if not cls.value_exists(run=run):
#                 cls.objects.create(value=val, run=run)
#                 added += 1
#         return added
#
#     @classmethod
#     def value_exists(cls, run):
#         return cls.objects.filter(run=run).exists()
#
#     def __str__(self):
#         return str(self.value)


class Email(models.Model):
    name = models.CharField(max_length=100)
    address = models.EmailField(max_length=200)
    project = models.ManyToManyField(Project)

    #     @classmethod
    #     def add_email(cls, name, address):
    #         return cls.objects.create(name=name, address=address)
    #
    #     @classmethod
    #     def send_message_email(cls, file_name):
    #         mailing_list = []
    #         emails = cls.objects.all()
    #         for email in emails:
    #             mailing_list.append(email.address)
    #
    #         email_html_file = '<h4>Please see attached pdf report file</h4>'
    #         msg = EmailMessage('mCRAG weekly report', email_html_file, settings.EMAIL_HOST_USER, mailing_list)
    #         msg.attach_file(file_name)
    #         msg.content_subtype = "html"
    #         return msg.send()
    #
    def __unicode__(self):
        return str(self.name)


class Voice(models.Model):
    id = models.IntegerField(primary_key=True)
    uuid = models.CharField(max_length=50)
    project = models.ForeignKey(Project)
    contact = models.ForeignKey(Contact)
    reason = models.TextField()
    advice = models.TextField()
    created_by = models.CharField(max_length=100)
    created_on = models.DateTimeField(null=True)

    @classmethod
    def get_data(cls, proj):
        url = "http://voice.tmcg.co.ug/~nicholas/data.php?project={0}".format(urllib2.quote(proj))
        req = urllib2.Request(url)
        response = urllib2.urlopen(req)
        datas = json.load(response)
        for data in datas:
            if cls.voice_id_exists(id=data['id']):
                pass
            else:
                urns = cls.clean_contact(data['phone_number'])
                if Contact.urns_exists(number=urns):
                    uuid = hashlib.md5(data['created_at']).hexdigest()
                    obj = Contact.objects.filter(urns=urns).first()
                    pro = Project.objects.get(name=proj)
                    cls.objects.create(id=data['id'], uuid=uuid, project=pro, contact=obj,
                                       reason=data['reason_for_call'],
                                       advice=data['advice_given'], created_by=data['created_by'],
                                       created_on=data['created_at'])
                else:
                    pass

        return datas

    @classmethod
    def voice_id_exists(cls, id):
        return cls.objects.filter(uuid=id).exists()

    @classmethod
    def clean_contact(cls, contact):
        c = ''
        if len(contact) == 10:
            c = '+256' + contact[1:]
        elif len(contact) == 12:
            c = '+' + contact
        elif len(contact) == 9:
            c = '+256' + contact
        else:
            pass
        return c

    def __unicode__(self):
        return str(self.project)
