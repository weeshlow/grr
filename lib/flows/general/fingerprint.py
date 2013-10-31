#!/usr/bin/env python
# Copyright 2012 Google Inc. All Rights Reserved.
"""Invoke the fingerprint client action on a file."""


from grr.lib import aff4
from grr.lib import flow
from grr.lib import rdfvalue
from grr.proto import flows_pb2


class FingerprintFileArgs(rdfvalue.RDFProtoStruct):
  protobuf = flows_pb2.FingerprintFileArgs


class FingerprintFile(flow.GRRFlow):
  """Retrieve all fingerprints of a file."""

  category = "/Filesystem/"
  args_type = FingerprintFileArgs
  behaviours = flow.GRRFlow.behaviours + "BASIC"

  @flow.StateHandler(next_state="Done")
  def Start(self):
    """Issue the fingerprinting request."""

    request = rdfvalue.FingerprintRequest(
        pathspec=self.args.pathspec)

    # Generic hash.
    request.AddRequest(
        fp_type=rdfvalue.FingerprintTuple.Type.FPT_GENERIC,
        hashers=[rdfvalue.FingerprintTuple.Hash.MD5,
                 rdfvalue.FingerprintTuple.Hash.SHA1,
                 rdfvalue.FingerprintTuple.Hash.SHA256])

    # Authenticode hash.
    request.AddRequest(
        fp_type=rdfvalue.FingerprintTuple.Type.FPT_PE_COFF,
        hashers=[rdfvalue.FingerprintTuple.Hash.MD5,
                 rdfvalue.FingerprintTuple.Hash.SHA1,
                 rdfvalue.FingerprintTuple.Hash.SHA256])

    self.CallClient("FingerprintFile", request, next_state="Done")

  @flow.StateHandler()
  def Done(self, responses):
    """Store the fingerprint response."""
    if not responses.success:
      # Its better to raise rather than merely logging since it will make it to
      # the flow's protobuf and users can inspect the reason this flow failed.
      raise flow.FlowError("Could not fingerprint file: %s" % responses.status)

    response = responses.First()
    if response.pathspec.path:
      urn = aff4.AFF4Object.VFSGRRClient.PathspecToURN(response.pathspec,
                                                       self.client_id)
    else:
      urn = aff4.AFF4Object.VFSGRRClient.PathspecToURN(self.args.pathspec,
                                                       self.client_id)
    self.state.Register("urn", urn)

    fd = aff4.FACTORY.Create(urn, "VFSFile", mode="w", token=self.token)

    hash_obj = fd.Schema.HASH()

    for result in response.results:
      if result["name"] == "generic":
        for hash_type in ["md5", "sha1", "sha256"]:
          value = result.GetItem(hash_type)
          if value:
            setattr(hash_obj, hash_type, value)

      if result["name"] == "pecoff":
        for hash_type in ["md5", "sha1", "sha256"]:
          value = result.GetItem(hash_type)
          if value:
            setattr(hash_obj, "pecoff_" + hash_type, value)

        signed_data = result.GetItem("SignedData", [])
        for data in signed_data:
          hash_obj.signed_data.Append(
              revision=data[0], cert_type=data[1], certificate=data[2])

    fd.Set(hash_obj)

    # TODO(user): This attribute will be deprecated in the future. Do not
    # use.
    fd.Set(fd.Schema.FINGERPRINT(response))
    fd.Close(sync=False)

  @flow.StateHandler()
  def End(self):
    """Finalize the flow."""
    self.Notify("ViewObject", self.state.urn, "Fingerprint retrieved.")
    self.Status("Finished fingerprinting %s", self.args.pathspec.path)
    # Notify any parent flows.
    self.SendReply(self.state.urn)
