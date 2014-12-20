from functools import wraps
import pytz

from dateutil.relativedelta import relativedelta

from django.test import TestCase
from django.utils import unittest
from django.utils.timezone import datetime, timedelta

from allocation import engine
from allocation.models import Provider, Machine, Size, Instance, InstanceHistory
from allocation.models import Allocation,\
        MultiplySizeCPU, MultiplySizeRAM,\
        MultiplySizeDisk, MultiplyBurnTime,\
        AllocationIncrease, AllocationRecharge, TimeUnit,\
        IgnoreStatusRule, CarryForwardTime, validate_interval

from core.models import Instance as CoreInstance

#For testing..

#Input Placeholders
openstack = Provider(
        name="iPlant Cloud - Tucson",
        identifier="4")
openstack_workshop = Provider(
        name="iPlant Cloud Workshop - Tucson",
        identifier="5")

random_machine = Machine(
        name="Not real machine",
        identifier="12412515-1241-3fc8-bc13-10b03d616c54")
random_machine_2 = Machine(
        name="Not real machine",
        identifier="39966e54-9282-4fc8-bc13-10b03d616c54")


tiny_size = Size(name='Kids Fry', identifier='test.tiny', cpu=1, ram=1024*2, disk=0)
small_size = Size(name='Small Fry', identifier='test.small', cpu=2, ram=1024*4, disk=60)
medium_size = Size(name='Medium Fry', identifier='test.medium', cpu=4, ram=1024*16, disk=120)
large_size = Size(name='Large Fry', identifier='test.large', cpu=8, ram=1024*32, disk=240)


AVAILABLE_PROVIDERS = {
    "openstack": openstack,
    "workshop": openstack_workshop
}


AVAILABLE_MACHINES = {
    "machine1": random_machine,
    "machine2": random_machine_2,
}


AVAILABLE_SIZES = {
    "test.tiny": tiny_size,
    "test.small": small_size,
    "test.medium": medium_size,
    "test.large": large_size
}

STATUS_CHOICES = frozenset(["active", "suspended"])

# Rules
carry_forward = CarryForwardTime()

multiply_by_ram = MultiplySizeRAM(
        name="Multiply TimeUsed by Ram (*1GB)", multiplier=(1/1024))
multiply_by_cpu = MultiplySizeCPU(
        name="Multiply TimeUsed by CPU", multiplier=1)
multiply_by_disk = MultiplySizeDisk(
        name="Multiply TimeUsed by Disk", multiplier=1)

half_usage_by_ram = MultiplySizeRAM(
        name="Multiply TimeUsed by 50% of Ram (GB)",
        multiplier=.5*(1/1024) )
half_usage_by_cpu =  MultiplySizeCPU(
        name="Multiply TimeUsed by 50% of CPU",
        multiplier=.5)
half_usage_by_disk = MultiplySizeDisk(
        name="Multiply TimeUsed by 50% of Disk",
        multiplier=.5)

zero_burn_rate = MultiplyBurnTime(name="Stop all Total Time Used", multiplier=0.0)
half_burn_rate = MultiplyBurnTime(name="Half-Off Total Time Used", multiplier=0.5)
double_burn_rate = MultiplyBurnTime(name="Double Total Time Used", multiplier=2.0)

ignore_inactive = IgnoreStatusRule("Ignore Inactive Instances", value=["build", "pending",
    "hard_reboot", "reboot",
     "migrating", "rescue",
     "resize", "verify_resize",
    "shutoff", "shutting-down",
    "suspended", "terminated",
    "deleted", "error", "unknown","N/A",
    ])
ignore_suspended = IgnoreStatusRule("Ignore Suspended Instances", "suspended")
ignore_build = IgnoreStatusRule("Ignore 'Build' Instances", "build")

class InstanceHelper(object):
    def __init__(self, provider="openstack", machine="machine1"):
        if provider not in AVAILABLE_PROVIDERS:
            raise Exception("The test provider specified is not a valid provider")

        if machine not in AVAILABLE_MACHINES:
            raise Exception("The test machine specified is not a valid machine")

        self.provider = AVAILABLE_PROVIDERS[provider]
        self.machine = AVAILABLE_MACHINES[machine]
        self.history = []

    def add_history_entry(self, start, end, size="test.small", status="active"):
        """
        Add a new history entry to the instance
        """
        if size not in AVAILABLE_SIZES:
            raise Exception("The test size specified is not a valid size")

        if status not in STATUS_CHOICES:
            raise Exception("The test status specified is not a valid status")

        new_history = InstanceHistory(
            status=status,
            size=AVAILABLE_SIZES[size],
            start_date=start,
            end_date=end)

        self.history.append(new_history)

    def to_instance(self, identifier):
        """
        Returns a new Instance
        or `raises` an Exception if the instance has no history
        """
        if not self.history:
            raise Exception("This instance requires at least one history entry.")

        return Instance(
            identifier=identifier,
            provider=self.provider,
            machine=self.machine,
            history=self.history)


class AllocationHelper():
    def __init__(self, start_window, end_window, credit_hours=1000):
        self.start_window = start_window
        self.end_window = end_window
        self.instances = []

        # Add default credits
        self.credits = [
            AllocationIncrease(
                name="Add %s Hours " % credit_hours,
                unit=TimeUnit.hour,
                amount=credit_hours,
                increase_date=self.start_window)
        ]

        # Add a default set of rules
        self.rules = [
            multiply_by_cpu,
            ignore_suspended,
            ignore_build,
            carry_forward
        ]

    def set_window(self, start_window, end_window):
        self.start_window = start_window
        self.end_window = end_window

    def add_instance(self, instance):
        if not isinstance(instance, Instance):
            raise TypeError("Expected type Instance got %s", type(instance))

        self.instances.append(instance)

    def add_rule(self, rule):
        if not isinstance(instance, Rule):
            raise TypeError("Expected type Rule got %s", type(instance))

        self.rules.append(rule)

    def add_credit(self, credit):
        if not isinstance(instance, AllocationIncrease):
            raise TypeError("Expected type AllocationIncrease got %s", type(instance))

        self.credits.append(credit)


class AllocationTestCase(unittest.TestCase):
    def _calculate_allocation(allocation):
        """
        Returns the allocation result
        """
        return engine.calculate_allocation(allocation)

    def assertOverAllocation(self, allocation):
        """
        Assert that the allocation is over allocation
        """
        allocation_result = self._calculate_allocation(allocation)
        self.assertTrue(self.allocation_result.over_allocation())
        return self

    def assertCreditEquals(self, allocation, credit):
        """
        Assert that the remaining credit matches for the allocation
        """
        allocation_result = self._calculate_allocation(allocation)
        self.assertEqual(allocation_result.total_credit(), credit)
        return self

    def assertTotalRuntimeEquals(self, allocation, total_runtime):
        """
        Assert that the total runtime matches the allocation
        """
        allocation_result = self._calculate_allocation(allocation)
        self.assertEqual(allocation_result.total_runtime(), total_runtime)
        return self

    def assertDifferenceEquals(self, allocation, difference):
        """
        Assert that the difference and the allocation matches
        """
        allocation_result = self._calculate_allocation(allocation)
        self.assertEquals(allocation_result, difference)
        return self

#Dynamic Tests
def test_instances(instance_ids, window_start, window_stop, credits=[], rules=[]):
    """
    """
    instance_list = []
    for instance_id in instance_ids:
        core_instance = CoreInstance.objects.get(provider_alias=instance_id)
        instance_list.append(Instance.from_core(core_instance))
    return test_allocation(credits, rules, instance_list,
            window_start, window_stop, interval_delta=None)
#Helper Tests
def create_allocation_test(
        window_start, window_stop,
        history_start, history_stop, 
        credits, rules, 
        swap_days=None, count=None, interval_date=None):
    """
    Create your own allocation test!
    Define a window (two datetimes)!
    Create a (Lot of) instance(s) and history!
    PASS IN your rules and credits!
    Swap between inactive/active status (If you want)!
    Create >1 instance with count!
    Set your own TimePeriod interval!
    """
    instances = instance_swap_status_test(
            history_start, history_stop, swap_days,
            size=medium_size, count=count)
    result = test_allocation(credits, rules, instances,
            window_start, window_stop, interval_date)
    return result

def test_allocation(credits, rules, instances,
                    window_start, window_stop, interval_delta=None):
    allocation_input = Allocation(
        credits=credits,
        rules=rules,
        instances=instances,
        start_date=window_start, end_date=window_stop,
        interval_delta=interval_delta
    )
    allocation_result = calculate_allocation(allocation_input)
    return allocation_result

def instance_swap_status_test(history_start, history_stop, swap_days,
                              provider=None, machine=None, size=None, count=1):
    """
    Instance swaps from active/suspended every swap_days,
    Starting 'active' on history_start
    """
    history_list = _build_history_list(history_start, history_stop,
            ["active","suspended"], tiny_size, timedelta(3))
    if not provider:
        provider = openstack
    if not machine:
        machine = random_machine
    instances = []
    for idx in xrange(1,count+1):#IDX 1
        instance = Instance(
            identifier="Test-Instance-%s" % idx,
            provider=provider, machine=machine,
            history=history_list)
        instances.append(instance)
    return instances

def _build_history_list(history_start, history_stop, status_choices=[],
        size=None,  swap_days=None):
    history_list = []

    #Good defaults:
    if not status_choices:
        status_choices = ["active","suspended"]
    if not size:
        size = tiny_size
    if not swap_days:
        #Will be 'active' status, full history on defaults.
        new_history = InstanceHistory(
             status=status_choices[0], size=size,
             start_date=history_start,
             end_date=history_start+swap_days)
        return new_history

    history_next = history_start + swap_days
    next_idx = 0
    status_len = len(status_choices)
    while history_next < history_stop:
        status_choice = status_choices[next_idx]
        next_idx = (next_idx + 1) % status_len
        new_history = InstanceHistory(
             status=status_choice, size=size,
             start_date=history_start,
             end_date=history_start+swap_days)
        history_list.append(new_history)
        #Toggle/Update..
        history_start = history_next
        history_next += swap_days
    return history_list


#Static tests
def run_test_1():
    """
    Test 1:
    Window set at 5 months (7/1/14 - 12/1/14)
    One-time credit of 10,000 AU (7/1)
    One instance running for ~5 months (Not quite because of 3-day offset)
    Assertions:
    When Dividing time into three different intervals:
    (Cumulative, Monthly+Rollover, n-days+Rollover)
    * allocation_credit, total_runtime(), and total_difference() are IDENTICAL.
      (NO TIME LOSS)
    """
    #Allocation Window
    window_start = datetime(2014,7,1, tzinfo=pytz.utc)
    window_stop = datetime(2014,12,1, tzinfo=pytz.utc)
    #Instances
    count = 1
    swap_days = timedelta(3)
    history_start = datetime(2014,7,4,hour=12, tzinfo=pytz.utc)
    history_stop = datetime(2014,12,4,hour=12, tzinfo=pytz.utc)
    #Allocation Credits
    achieve_greatness = AllocationIncrease(
            name="Add 10,000 Hours ",
            unit=TimeUnit.hour, amount=10000,
            increase_date=window_start)
    credits = [achieve_greatness]

    rules = [multiply_by_cpu, ignore_suspended, ignore_build, carry_forward]

    interval_days = None
    print "Running Cumulative Test"
    result_1 = create_allocation_test(window_start, window_stop, history_start,
            history_stop, credits, rules, swap_days, count, interval_days)

    print "Running timedelta Test"
    interval_days = timedelta(21)
    result_2 = create_allocation_test(window_start, window_stop, history_start,
            history_stop, credits, rules, swap_days, count, interval_days)

    print "Running relativedelta Test"
    interval_days = relativedelta(day=1, months=1)
    result_3 = create_allocation_test(window_start, window_stop, history_start,
            history_stop, credits, rules, swap_days, count, interval_days)

    test_1 = result_1.over_allocation()
    test_2 = result_2.over_allocation()
    test_3 = result_3.over_allocation()
    if test_1 != test_2 != test_3:
        raise Exception("Mismatch on Over-Allocation Result: "
                "Cumulative:%s Timedelta:%s Relativedelta:%s"
                % (test_1, test_2, test_3))

    test_1 = result_1.total_runtime()
    test_2 = result_2.total_runtime()
    test_3 = result_3.total_runtime()
    if test_1 != test_2 != test_3:
        raise Exception("Mismatch on Total Runtime: "
                "Cumulative:%s Timedelta:%s Relativedelta:%s"
                % (test_1, test_2, test_3))

    test_1 = result_1.total_credit()
    test_2 = result_2.total_credit()
    test_3 = result_3.total_credit()
    if test_1 != test_2 != test_3:
        raise Exception("Mismatch on Total Allocation Credit Received: "
                "Cumulative:%s Timedelta:%s Relativedelta:%s"
                % (test_1, test_2, test_3))

    test_1 = result_1.total_difference()
    test_2 = result_2.total_difference()
    test_3 = result_3.total_difference()
    if test_1 != test_2 != test_3:
        raise Exception("Mismatch on Total Allocation: "
                "Cumulative:%s Timedelta:%s Relativedelta:%s"
                % (test_1, test_2, test_3))
    return True

def run_test2():
    """
    TODO: Setup some new constraints here..
    """
    #Allocation Window
    window_start = datetime(2014,7,1, tzinfo=pytz.utc)
    window_stop = datetime(2014,12,1, tzinfo=pytz.utc)
    #Instances
    swap_days = timedelta(3)
    history_start = datetime(2014,7,4,hour=12, tzinfo=pytz.utc)
    history_stop = datetime(2014,12,4,hour=12, tzinfo=pytz.utc)
    #Allocation Credits
    achieve_greatness = AllocationIncrease(
            name="Add 10,000 Hours ",
            unit=TimeUnit.hour, amount=10000,
            increase_date=window_start)

    instances = instance_swap_status_test(
            history_start, history_stop, swap_days,
            size=medium_size, count=1)
    credits=[achieve_greatness]
    rules=[multiply_by_cpu, ignore_suspended, ignore_build]
    result = test_allocation(credits, rules, instances,
            window_start, window_stop, interval_days)
    return result

"""
Examples I think will break things:
    1. start_date = 1/1, end_date = 1/31
    2. Instances use 7 days of allocation from 1/1 to 1/8
    3. User has his monthly allocation on 1/8 (14 days)
    4. Instances use 7 days of allocation from 1/8 to 1/15
Questions:
    2. What "AllocationIncreases" are valid if the dates occur PRIOR to the
    recharge_date?
       * I think they should be ignored, and given a new AllocationIncrease
       * with the remainder value (The amount of that increase used in the
       * month PRIOR).
    # Should step 2 be allowed in the engine, should invalid time periods flag
    # in some way??
"""
