from langchain_core.tools import tool
from app.sfsf_client import sfsf_get


@tool
def get_employee(user_id: str) -> dict:
    """Fetch a SuccessFactors employee profile by userId or username.

    Args:
        user_id: The SuccessFactors userId (e.g. '802981') or username (e.g. 'ghill')
    """
    return sfsf_get(
        "/odata/v2/User",
        {
            "$filter": f"userId eq '{user_id}' or username eq '{user_id}'",
            "$select": "userId,username,firstName,lastName,email,department,title,location,manager",
            "$expand": "manager",
        },
    )


@tool
def search_employees(query: str, top: int = 10) -> dict:
    """Search SuccessFactors employees by name, email, or username (substring match).

    Args:
        query: Name, email, or username fragment to search for
        top:   Maximum number of results to return (default 10)
    """
    return sfsf_get(
        "/odata/v2/User",
        {
            "$filter": f"substringof('{query}',firstName) or substringof('{query}',lastName) or substringof('{query}',email) or substringof('{query}',username)",
            "$select": "userId,username,firstName,lastName,email,department,title",
            "$top": str(top),
        },
    )


@tool
def get_org_chart(user_id: str, depth: int = 2) -> dict:
    """Fetch the direct reports hierarchy for a given manager userId.

    Args:
        user_id: The manager's SuccessFactors userId
        depth:   How many levels of direct reports to expand (default 2, max 3)
    """
    expand = "directReports"
    if depth >= 2:
        expand += "/directReports"
    if depth >= 3:
        expand += "/directReports"

    return sfsf_get(
        "/odata/v2/User",
        {
            "$filter": f"userId eq '{user_id}'",
            "$select": "userId,firstName,lastName,title,department",
            "$expand": expand,
        },
    )


@tool
def get_job_info(user_id: str) -> dict:
    """Fetch the current job information for a SuccessFactors employee.

    Args:
        user_id: The SuccessFactors userId
    """
    return sfsf_get(
        "/odata/v2/EmpJob",
        {
            "$filter": f"userId eq '{user_id}'",
            "$select": "userId,jobTitle,department,division,location,startDate,managerId,jobCode",
            "$orderby": "startDate desc",
            "$top": "1",
        },
    )


@tool
def get_time_off_balance(user_id: str) -> dict:
    """Fetch time-off / leave balance for a SuccessFactors employee.

    Args:
        user_id: The SuccessFactors userId
    """
    return sfsf_get(
        "/odata/v2/TimeAccountDetail",
        {
            "$filter": f"externalCode eq '{user_id}'",
            "$select": "externalCode,timeAccountType,balance,unit",
        },
    )


@tool
def get_contract_renewal_cases(
    employee_id: str | None = None,
    status: str | None = None,
    department: str | None = None,
    faculty: str | None = None,
    top: int = 5,
) -> dict:
    """Fetch contract renewal cases from SuccessFactors.

    Use this tool to look up employment contract renewals, renewal dates, salary
    adjustments, performance grades, approval/review workflow status, and case notes
    for employees. Also covers vendor agreements and internal service transfers.

    Args:
        employee_id: Filter by employee userId (e.g. '103266'). Use userId, not username — resolve via search_employees first if needed.
        status:      Filter by case status (e.g. 'READY_FOR_SS_REVIEW', 'SS_REVIEWED_APPROVED', 'HRP_VALIDATED')
        department:  Filter by department code (e.g. 'ME')
        faculty:     Filter by faculty code (e.g. 'FoE')
        top:         Maximum number of results to return (default 5)
    """
    filters = []
    if employee_id:
        filters.append(f"cust_Employee eq '{employee_id}'")
    if status:
        filters.append(f"cust_status eq '{status}'")
    if department:
        filters.append(f"cust_department eq '{department}'")
    if faculty:
        filters.append(f"cust_faculty eq '{faculty}'")

    params = {
        "$select": (
            "externalCode,externalName,cust_Employee,cust_department,cust_faculty,"
            "cust_designation,cust_currentSalary,cust_newSalary,cust_salaryAdjustment,"
            "cust_performanceGrade,cust_currentEndDate,cust_proposedEndDate,"
            "cust_durationMonths,cust_discussionDate,cust_status,cust_notes,"
            "lastModifiedDateTime,lastModifiedBy,createdDateTime,createdBy"
        ),
        "$orderby": "lastModifiedDateTime desc",
        "$top": str(top),
    }
    if filters:
        params["$filter"] = " and ".join(filters)

    result = sfsf_get("/odata/v2/cust_ContractRenewalCaseV2", params)

    if isinstance(result, dict) and not result.get("error"):
        records = result.get("d", {}).get("results", [])
        if not records:
            return {
                "message": "No contract renewal cases found",
                "filters_applied": {
                    "employee_id": employee_id,
                    "status": status,
                    "department": department,
                    "faculty": faculty,
                },
            }

    return result


def get_tools() -> list:
    """Return all tools available to the agent."""
    return [
        get_employee,
        search_employees,
        get_org_chart,
        get_job_info,
        get_time_off_balance,
        get_contract_renewal_cases,
    ]
