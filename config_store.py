from enum import Enum
from typing import OrderedDict, Set, Tuple

import yaml_parser
from api_key_manager import CCloudAPIKeyList
from clusters import CCloudClusterList
from secrets_manager_interface import CSMSecretsManager
from service_account import CCloudServiceAccountList


class CSMConfigTaskStatus(Enum):
    sts_not_started = "Not Started"
    sts_in_progress = "In Progress"
    sts_success = "Success"
    sts_failed = "Failed"


class CSMConfigTaskType(Enum):
    create_task = "create"
    update_task = "update"
    delete_task = "delete"


class CSMConfigObjectType(Enum):
    sa_type = "service-account"
    api_key_type = "api-key"
    secret_store_type = "secrets-store"


class CSMConfigTask:
    def __init__(
        self, task_type, object_type, status, task_object: dict, status_msg: str = "Waiting to start"
    ) -> None:
        self.task_type = task_type
        self.object_type = object_type
        self.status = status
        self.status_message = status_msg
        self.task_object = task_object


class CSMConfigDataMap:
    tasks: OrderedDict[int, CSMConfigTask]

    def __init__(self) -> None:
        self.tasks = {}

    def add_new_task(
        self, task_type, object_type, status, task_object: dict, status_msg: str = "Waiting to start"
    ) -> bool:
        self.tasks[len(self.tasks)] = CSMConfigTask(task_type, object_type, status, task_object, status_msg)

    def set_task_status(
        self, task_key: int, task_status, status_msg: str, object_payload: dict = None
    ) -> CSMConfigTask:
        self.tasks[task_key].status = task_status
        self.tasks[task_key].status_message = status_msg
        if object_payload:
            self.tasks[task_key].task_object = object_payload
        self.print_task_data(task_key, self.tasks[task_key])
        return self.tasks[task_key]

    def select_new_tasks(self):
        for k, v in self.tasks.items():
            if v.status == CSMConfigTaskStatus.sts_not_started:
                yield k, v

    def populate_service_account_tasks(
        self,
        csm_configs: yaml_parser.CSMConfig,
        csm_definitions: yaml_parser.CSMDefinitions,
        ccloud_sa_details: CCloudServiceAccountList,
    ):
        sa_in_def = set([v.name for v in csm_definitions.sa])
        rp_user_sa_in_def = set([v.name for v in csm_definitions.sa if v.is_rp_user])
        non_rp_user_sa_in_def = set([v.name for v in csm_definitions.sa if not v.is_rp_user])
        sa_in_ccloud = set([v.name for v in ccloud_sa_details.sa.values()])
        # create_req = set(sa_in_def).difference(sa_in_ccloud)
        rp_create_req = set(rp_user_sa_in_def).difference(sa_in_ccloud)
        non_rp_create_req = set(non_rp_user_sa_in_def).difference(sa_in_ccloud)
        delete_req = set(sa_in_ccloud).difference(sa_in_def)

        for item in rp_create_req:
            sa = csm_definitions.find_service_account(item)
            if sa:
                self.add_new_task(
                    CSMConfigTaskType.create_task,
                    CSMConfigObjectType.sa_type,
                    CSMConfigTaskStatus.sts_not_started,
                    {"sa_name": sa.name, "description": sa.description},
                )
        for item in non_rp_create_req:
            sa = csm_definitions.find_service_account(item)
            if sa:
                self.add_new_task(
                    CSMConfigTaskType.create_task,
                    CSMConfigObjectType.sa_type,
                    CSMConfigTaskStatus.sts_not_started,
                    {"sa_name": sa.name, "description": sa.description},
                )
        if csm_configs.ccloud.enable_sa_cleanup:
            for item in delete_req:
                # sa = definitions.find_service_account(item)
                # if not sa:
                self.add_new_task(
                    CSMConfigTaskType.delete_task,
                    CSMConfigObjectType.sa_type,
                    CSMConfigTaskStatus.sts_not_started,
                    {"sa_name": item},
                )

    def populate_api_key_tasks(
        self,
        csm_definitions: yaml_parser.CSMDefinitions,
        ccloud_sa_details: CCloudServiceAccountList,
        ccloud_api_key_details: CCloudAPIKeyList,
        ccloud_clusters: CCloudClusterList,
        secret_list: CSMSecretsManager,
    ) -> Tuple[Set[str], Set[str]]:
        api_keys_in_def, api_keys_in_ccloud = set(), set()
        secrets_in_store = set()
        for sa in csm_definitions.sa:
            if "FORCE_ALL_CLUSTERS" in sa.cluster_list:
                api_keys_in_def.update(["~".join([sa.name, v.cluster_id]) for v in ccloud_clusters.cluster.values()])
            else:
                api_keys_in_def.update(["~".join([sa.name, v]) for v in sa.cluster_list])
            sa_id = ccloud_sa_details.find_sa(sa.name)
            api_keys_in_ccloud.update(
                [
                    "~".join([sa.name, v.cluster_id])
                    for v in ccloud_api_key_details.find_keys_with_sa(getattr(sa_id, "resource_id", None))
                ]
            )
        create_api_keys_req = api_keys_in_def.difference(api_keys_in_ccloud)
        secrets_in_store.update(["~".join([v.sa_name, v.cluster_id]) for v in secret_list.secret.values()])
        create_secrets_req = api_keys_in_def.difference(secrets_in_store)
        update_secrets_req = create_api_keys_req.intersection(secrets_in_store)
        # This is needed if the Secret does not exist but an API key exists for the cluster.
        # As the secret cannot be retrieved after the first time its created, there is no way
        # to inject the secret to a Secret store in case of any failures. The API Key will need
        # to be freshly created and synced to the Secret Store.
        force_api_key_create = create_secrets_req.difference(create_api_keys_req)
        create_api_keys_req.update(force_api_key_create)
        for item in create_api_keys_req:
            value = item.split("~", 1)
            sa_name, cluster_id = value[0], value[1]
            sa_details = ccloud_sa_details.find_sa(sa_name)
            cluster_details = ccloud_clusters.find_cluster(cluster_id)
            api_keys = ccloud_api_key_details.find_keys_with_sa_and_cluster(
                getattr(sa_details, "resource_id", None), cluster_details.cluster_id
            )
            self.add_new_task(
                CSMConfigTaskType.create_task,
                CSMConfigObjectType.api_key_type,
                CSMConfigTaskStatus.sts_not_started,
                {"sa_name": sa_name, "cluster_id": cluster_details.cluster_id, "env_id": cluster_details.env_id},
            )
        return create_secrets_req, update_secrets_req

    def populate_secrets_tasks(
        self,
        create_secrets_req: Set[str],
        update_secrets_req: Set[str],
        ccloud_clusters: CCloudClusterList,
        csm_definitions: yaml_parser.CSMDefinitions,
    ):
        for item in create_secrets_req:
            value = item.split("~", 1)
            sa_name, cluster_id = value[0], value[1]
            cluster_details = ccloud_clusters.find_cluster(cluster_id)
            sa_definition = csm_definitions.find_service_account(sa_name)
            self.add_new_task(
                CSMConfigTaskType.create_task,
                CSMConfigObjectType.secret_store_type,
                CSMConfigTaskStatus.sts_not_started,
                {
                    "sa_name": sa_name,
                    "cluster_id": cluster_id,
                    "env_id": cluster_details.env_id,
                    "need_rp_access": sa_definition.rp_access,
                    "is_rp_user": sa_definition.is_rp_user,
                },
            )
        for item in update_secrets_req:
            value = item.split("~", 1)
            sa_name, cluster_id = value[0], value[1]
            cluster_details = ccloud_clusters.find_cluster(cluster_id)
            self.add_new_task(
                CSMConfigTaskType.update_task,
                CSMConfigObjectType.secret_store_type,
                CSMConfigTaskStatus.sts_not_started,
                {
                    "sa_name": sa_name,
                    "cluster_id": cluster_id,
                    "env_id": cluster_details.env_id,
                    "need_rp_access": sa_definition.rp_access,
                    "is_rp_user": sa_definition.is_rp_user,
                },
            )

    # This function is used to populate the tasks that will be executed during this run.
    def populate_data_map(
        self,
        definitions: yaml_parser.CSMDefinitions,
        ccloud_sa_details: CCloudServiceAccountList,
        ccloud_api_key_details: CCloudAPIKeyList,
        ccloud_clusters: CCloudClusterList,
        csm_configs: yaml_parser.CSMConfig,
        secret_list: CSMSecretsManager,
        is_api_mgmt_disabled: bool,
    ):
        #  Analyze and set up tasks for Service Account management
        self.populate_service_account_tasks(csm_configs, definitions, ccloud_sa_details)

        if not is_api_mgmt_disabled:
            # Analyze and setup tasks for API Key management
            create_secrets_req, update_secrets_req = self.populate_api_key_tasks(
                definitions, ccloud_sa_details, ccloud_api_key_details, ccloud_clusters, secret_list
            )

            # Analyze and setup tasks for Secret Store
            self.populate_secrets_tasks(create_secrets_req, update_secrets_req, ccloud_clusters, definitions)
        else:
            print(
                "As API Key creation flag is disabled, the API Key creation and Secret management functionality will not be triggered."
            )

    def print_data_map(self, include_create: bool = True, include_delete: bool = True):
        print("=" * 80)
        print(
            "{:<3} {:<10} {:<17} {:<15} {:<30} {:<50}".format(
                "S# ", "Task Type", "Object Type", "Current Status", "Status Message", "Object Payload"
            )
        )
        for k, v in self.tasks.items():
            if (v.task_type == CSMConfigTaskType.create_task and include_create) or (
                v.task_type == CSMConfigTaskType.delete_task and include_delete
            ):
                print(
                    "{:<3} {:<10} {:<17} {:<15} {:<30} {:<50}".format(
                        k, v.task_type.value, v.object_type.value, v.status.value, v.status_message, str(v.task_object)
                    )
                )
        print("=" * 80)

    def print_task_data(self, task_key: int, task_object: CSMConfigTask):
        print(
            "{:<3} {:<10} {:<17} {:<15} {:<30} {:<50}".format(
                task_key,
                task_object.task_type.value,
                task_object.object_type.value,
                task_object.status.value,
                task_object.status_message,
                str(task_object.task_object),
            )
        )
