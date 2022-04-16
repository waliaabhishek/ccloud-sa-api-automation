import itertools
from dataclasses import dataclass, field

import app_managers.core.types as CoreTypes
from app_managers.workflow_manager.task_generator import CSMAPIKeyTasks, CSMSecretManagerTasks, CSMServiceAccountTasks
from app_managers.workflow_manager.types import CSMConfigTaskStatus, CSMConfigTaskType
from ccloud_managers.types import CCloudConfigBundle
from secret_managers.types import CSMSecretsManager
from app_managers.helpers import printline


@dataclass(kw_only=True)
class WorkflowManager:
    csm_bundle: CoreTypes.CSMYAMLConfigBundle
    ccloud_bundle: CCloudConfigBundle
    secret_bundle: CSMSecretsManager
    dry_run: bool
    sa_tasks: CSMServiceAccountTasks = field(init=False)
    api_key_tasks: CSMAPIKeyTasks = field(init=False)
    secret_tasks: CSMSecretManagerTasks = field(init=False)

    def __post_init__(self) -> None:
        self.sa_tasks = CSMServiceAccountTasks(csm_bundle=self.csm_bundle, ccloud_bundle=self.ccloud_bundle)
        self.api_key_tasks = CSMAPIKeyTasks(
            csm_bundle=self.csm_bundle, ccloud_bundle=self.ccloud_bundle, secret_bundle=self.secret_bundle
        )
        self.secret_tasks = CSMSecretManagerTasks(
            csm_bundle=self.csm_bundle,
            ccloud_bundle=self.ccloud_bundle,
            api_key_tasks=self.api_key_tasks,
            secret_bundle=self.secret_bundle,
        )

    def create_service_accounts(self):
        printline()
        print(f"Triggering Service Account creation Workflow. Dry Run flag: {self.dry_run}")
        self.sa_tasks.refresh_set_values(csm_bundle=self.csm_bundle, ccloud_bundle=self.ccloud_bundle)
        for item in self.sa_tasks.create_service_account_tasks():
            item.print_task_data()
            if not self.dry_run:
                new_sa, is_success = self.ccloud_bundle.cc_service_accounts.create_sa(
                    sa_name=item.task_object["sa_name"],
                    description=item.task_object["description"],
                )
                if is_success:
                    item.set_task_status(
                        task_status=CSMConfigTaskStatus.sts_success,
                        status_msg="Service Account Creation Succeeded.",
                        object_payload={"sa_id": new_sa.resource_id, "sa_name": new_sa.name},
                    )

    def delete_service_accounts(self):
        printline()
        print(f"Triggering Service Account deletion Workflow. Dry Run flag: {self.dry_run}")
        self.sa_tasks.refresh_set_values(csm_bundle=self.csm_bundle, ccloud_bundle=self.ccloud_bundle)
        for item in self.sa_tasks.delete_service_account_tasks():
            item.print_task_data()
            if not self.dry_run:
                sa_id = self.ccloud_bundle.cc_service_accounts.find_sa(item.task_object["sa_name"]).resource_id
                is_success = self.ccloud_bundle.cc_service_accounts.delete_sa(item.task_object["sa_name"])
                if is_success:
                    item.set_task_status(
                        task_status=CSMConfigTaskStatus.sts_success,
                        status_msg="Service Account deletion Succeeded.",
                        object_payload={"sa_id": sa_id, "sa_name": item.task_object["sa_name"]},
                    )

    def create_api_keys(self):
        printline()
        print(f"Triggering API Key creation workflow. Dry Run flag: {self.dry_run}")
        self.api_key_tasks.refresh_set_values(csm_bundle=self.csm_bundle, ccloud_bundle=self.ccloud_bundle)
        for item in self.api_key_tasks.create_api_key_tasks():
            item.print_task_data()
            if not self.dry_run:
                sa_details = self.ccloud_bundle.cc_service_accounts.find_sa(item.task_object["sa_name"])
                new_api_key, is_success = self.ccloud_bundle.cc_api_keys.create_api_key(
                    env_id=item.task_object["env_id"],
                    cluster_id=item.task_object["cluster_id"],
                    sa_id=sa_details.resource_id,
                    sa_name=sa_details.name,
                    description=f"API Key for sa {sa_details.resource_id} created by the CI/CD workflow",
                )
                if is_success:
                    item.set_task_status(
                        task_status=CSMConfigTaskStatus.sts_success,
                        status_msg="API Key creation succeeded.",
                        object_payload={
                            "api_key": new_api_key["key"],
                            "env_id": item.task_object["env_id"],
                            "cluster_id": item.task_object["cluster_id"],
                        },
                    )

    def delete_api_keys(self):
        printline()
        print(f"Triggering API Key deletion workflow. Dry Run flag: {self.dry_run}")
        self.api_key_tasks.refresh_set_values(csm_bundle=self.csm_bundle, ccloud_bundle=self.ccloud_bundle)
        for item in self.api_key_tasks.delete_api_key_tasks():
            item.print_task_data()
            if not self.dry_run:
                is_success = self.ccloud_bundle.cc_api_keys.delete_api_key(api_key=item.task_object["api_key"])
                if is_success:
                    item.set_task_status(
                        task_status=CSMConfigTaskStatus.sts_success,
                        status_msg="API Key deletion succeeded.",
                        object_payload=item.task_object,
                    )

    def update_api_keys_in_secret_manager(self):
        printline()
        print(f"Triggering Secret Manager Update workflow. Dry Run flag: {self.dry_run}")
        self.secret_tasks.refresh_set_values(api_key_tasks=self.api_key_tasks)
        for item in itertools.chain(self.secret_tasks.create_secret_tasks(), self.secret_tasks.update_secret_tasks()):
            item.print_task_data()
            if not self.dry_run:
                sa_details = self.ccloud_bundle.cc_service_accounts.find_sa(item.task_object["sa_name"])
                api_key_details = self.ccloud_bundle.cc_api_keys.find_keys_with_sa_and_cluster(
                    sa_details.resource_id, item.task_object["cluster_id"]
                )
                for api_key in api_key_details:
                    if api_key.api_secret:
                        resp = self.secret_bundle.create_or_update_secret(api_key=api_key)
                        item.set_task_status(
                            task_status=CSMConfigTaskStatus.sts_success,
                            status_msg="Secret Updated Successfully",
                            object_payload={
                                "secret_name": resp.secret_name,
                                "sa_name": resp.sa_name,
                                "sa_id": resp.sa_id,
                                "cluster_id": resp.cluster_id,
                                "api_key": resp.api_key,
                            },
                        )

    def update_tags_in_secret_manager(self) -> bool:
        printline()
        print(f"Triggering Secret Manager Rest Proxy Tags Reconciliation workflow. Dry Run flag: {self.dry_run}")
        self.secret_tasks.refresh_set_values(api_key_tasks=self.api_key_tasks)
        for item in self.secret_tasks.update_secret_tags_tasks():
            item.print_task_data()
            if not self.dry_run:
                self.secret_bundle.add_tags(
                    secret_name=item.task_object["secret_name"],
                    tags={"rest_proxy_access": item.task_object["rest_proxy_access"], "sync_needed_for_rp": True},
                )
                secret_details = self.secret_bundle.secret[item.task_object["secret_name"]]
                secret_details.sync_needed_for_rp = True
                secret_details.rp_access = item.task_object["rest_proxy_access"]
                item.set_task_status(
                    task_status=CSMConfigTaskStatus.sts_success,
                    status_msg="Secret Tags Updated Successfully",
                    object_payload=item.task_object,
                )

    def update_rest_proxy_api_keys_in_secret_manager(self) -> bool:
        printline()
        print(f"Triggering Rest Proxy Update workflow. Dry Run flag: {self.dry_run}")
        self.secret_tasks.refresh_set_values(api_key_tasks=self.api_key_tasks)
        for item in self.secret_tasks.upsert_rest_proxy_secret_tasks():
            item.print_task_data()
            if not self.dry_run:
                self.secret_bundle.create_update_rest_proxy_secrets(
                    rp_secret_name=item.task_object["rp_secret_name"],
                    rp_sa_details=item.task_object["sa_details"],
                    rp_cluster_details=item.task_object["cluster_details"],
                    new_api_keys=[
                        v
                        for v in self.ccloud_bundle.cc_api_keys.api_keys.values()
                        if v.api_key in item.task_object["api_keys"]
                    ],
                    secrets_with_rp_access=[
                        v
                        for v in self.secret_bundle.secret.values()
                        if v.secret_name in item.task_object["secrets_with_rp_access"]
                    ],
                    is_rp_secret_new=True if item.task_type == CSMConfigTaskType.create_task else False,
                )
                item.set_task_status(
                    task_status=CSMConfigTaskStatus.sts_success,
                    status_msg="REST Proxy Secret Updated Successfully",
                    object_payload={
                        "rp_secret_name": item.task_object["rp_secret_name"],
                        "api_keys": item.task_object["api_keys"],
                        "secrets_with_rp_access": item.task_object["secrets_with_rp_access"],
                    },
                )
